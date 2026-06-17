"""Minitouch-based touch injection for Android.

Replaces ``adb shell input tap/swipe`` (which cold-starts a JVM per command,
~0.6s each, and ran backgrounded so concurrent injections interleaved into
unpaired DOWN/UP streams) with minitouch, which writes evdev events directly
to /dev/input. Single ordered socket + a single device-side process consuming
commands serially means DOWN/MOVE/UP are emitted under our explicit control and
are always paired.

Protocol reference (openstf/minitouch):
    - On connect the device sends a banner:
        v <version>
        ^ <max-contacts> <max-x> <max-y> <max-pressure>
        $ <pid>
    - Commands written back (each line; 'c' commits a frame):
        d <contact> <x> <y> <pressure>   # touch down
        m <contact> <x> <y> <pressure>   # move
        u <contact>                       # touch up
        c                                 # commit
        w <ms>                            # wait
"""

import asyncio
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .adb_protocol import AdbConnection, AdbProtocol

# Where the binary is pushed and the abstract socket name minitouch creates.
DEVICE_BINARY_PATH = "/data/local/tmp/minitouch"
ABSTRACT_SOCKET = "minitouch"


def scale(
    x: int,
    y: int,
    screen_w: int,
    screen_h: int,
    max_x: int,
    max_y: int,
) -> Tuple[int, int]:
    """Map display pixel coordinates to the touch device's coordinate space.

    minitouch expects coordinates in the evdev device's ABS_MT_POSITION range
    (max_x/max_y from the banner), which may differ from the display
    resolution. Linear scale + clamp. Screen rotation is not handled (natural
    orientation assumed).
    """
    if screen_w <= 1 or screen_h <= 1:
        return (max(0, min(x, max_x)), max(0, min(y, max_y)))
    tx = round(x * max_x / (screen_w - 1))
    ty = round(y * max_y / (screen_h - 1))
    tx = max(0, min(tx, max_x))
    ty = max(0, min(ty, max_y))
    return (tx, ty)


def build_swipe_steps(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int,
) -> Tuple[List[Tuple[int, int]], float]:
    """Interpolate a swipe into intermediate move points.

    Returns (points, step_sleep_seconds) where points are the move targets
    AFTER the initial down at (x1, y1) up to and including (x2, y2). Steps are
    chosen for ~60fps and capped so very long swipes don't explode.
    """
    n = duration_ms // 16
    n = max(2, min(n, 60))
    points: List[Tuple[int, int]] = []
    for i in range(1, n + 1):
        t = i / n
        px = round(x1 + (x2 - x1) * t)
        py = round(y1 + (y2 - y1) * t)
        points.append((px, py))
    step_sleep = (duration_ms / 1000.0) / n
    return (points, step_sleep)


@dataclass
class MinitouchBanner:
    """Parsed minitouch handshake values."""

    max_contacts: int
    max_x: int
    max_y: int
    max_pressure: int


def parse_banner(text: str) -> MinitouchBanner:
    """Parse the minitouch handshake banner.

    Looks for the '^ <max-contacts> <max-x> <max-y> <max-pressure>' line.
    Raises ValueError if it is absent or malformed.
    """
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("^"):
            parts = line.split()
            if len(parts) >= 5:
                return MinitouchBanner(
                    max_contacts=int(parts[1]),
                    max_x=int(parts[2]),
                    max_y=int(parts[3]),
                    max_pressure=int(parts[4]),
                )
    raise ValueError(f"minitouch banner missing '^' line: {text!r}")


class MinitouchSession:
    """Drives a device-side minitouch process over the ADB transport.

    Holds two connections to the ADB daemon, both switched to the device:
      - runner: runs ``shell:<binary>`` and stays open; closing it kills the
        device-side minitouch process.
      - control: connects to ``localabstract:minitouch``, reads the banner,
        and is the channel all d/m/u/c commands are written to.
    """

    def __init__(self, serial: str, host: str = "127.0.0.1", port: int = 5037):
        self.serial = serial
        self.host = host
        self.port = port
        self._runner: Optional[AdbConnection] = None
        self._control: Optional[AdbConnection] = None
        self.banner: Optional[MinitouchBanner] = None

    async def _open_transport(self, service: str) -> AdbConnection:
        """Open a connection, switch to the device, and request a service."""
        conn = AdbConnection(self.host, self.port)
        await conn.connect()
        await conn.write(AdbProtocol.encode_command(f"host:transport:{self.serial}"))
        await conn.check_okay()
        await conn.write(AdbProtocol.encode_command(service))
        await conn.check_okay()
        return conn

    async def open(self) -> None:
        """Start minitouch on the device and connect to its control socket.

        Raises on any failure (no silent fallback): the caller treats an
        exception as "minitouch unavailable" and aborts.
        """
        # Launch the device-side process; keep this connection open.
        self._runner = await self._open_transport(f"shell:{DEVICE_BINARY_PATH}")

        # The abstract socket appears shortly after launch - retry connecting.
        last_err: Optional[Exception] = None
        for _ in range(20):
            try:
                self._control = await self._open_transport(
                    f"localabstract:{ABSTRACT_SOCKET}"
                )
                break
            except Exception as e:  # noqa: BLE001 - retry any connect failure
                last_err = e
                await asyncio.sleep(0.1)
        if not self._control:
            raise ConnectionError(
                f"could not connect to minitouch socket: {last_err}"
            )

        # Read the banner so we know the touch coordinate space.
        self.banner = await self._read_banner()

    async def _read_banner(self) -> MinitouchBanner:
        """Read until the '$' (pid) line, then parse the banner."""
        assert self._control is not None
        buf = b""
        for _ in range(20):
            chunk = await self._control.read_bytes(1)
            buf += chunk
            # Banner ends with the '$ <pid>' line; wait for its newline.
            if b"$" in buf and buf.endswith(b"\n"):
                break
        return parse_banner(buf.decode("utf-8", errors="replace"))

    def _pressure(self) -> int:
        """A reasonable touch pressure within the device's range."""
        max_p = self.banner.max_pressure if self.banner else 0
        if max_p <= 0:
            return 50
        return min(50, max_p)

    async def tap(self, tx: int, ty: int) -> None:
        """Inject a single tap at already-scaled coordinates."""
        if not self._control:
            raise ConnectionError("minitouch control socket not open")
        p = self._pressure()
        cmd = f"d 0 {tx} {ty} {p}\nc\nu 0\nc\n"
        self._control.write_nowait(cmd.encode("utf-8"))

    async def swipe(
        self,
        tx1: int,
        ty1: int,
        points: List[Tuple[int, int]],
        step_sleep: float,
    ) -> None:
        """Inject a swipe: down at (tx1, ty1), then move through points, then up.

        Blocks for the full gesture duration so the next gesture cannot start
        mid-swipe (preserving DOWN/UP pairing) and so the scheduler's interval
        accounts for the real gesture time.
        """
        if not self._control:
            raise ConnectionError("minitouch control socket not open")
        p = self._pressure()
        self._control.write_nowait(f"d 0 {tx1} {ty1} {p}\nc\n".encode("utf-8"))
        for (px, py) in points:
            self._control.write_nowait(f"m 0 {px} {py} {p}\nc\n".encode("utf-8"))
            if step_sleep > 0:
                await asyncio.sleep(step_sleep)
        self._control.write_nowait(b"u 0\nc\n")

    async def close(self) -> None:
        """Lift any active contact and close both connections."""
        if self._control:
            try:
                self._control.write_nowait(b"u 0\nc\n")
            except Exception:
                pass
            await self._control.close()
            self._control = None
        if self._runner:
            await self._runner.close()
            self._runner = None
