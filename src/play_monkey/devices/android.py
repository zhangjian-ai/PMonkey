"""Android device implementation using minitouch for touch injection.

Touch events go through minitouch (direct evdev writes to /dev/input) rather
than ``adb shell input``: minitouch avoids a per-event JVM cold-start (~0.6s)
and lets us emit DOWN/MOVE/UP under explicit, serial control so they are always
paired. Non-input operations (state, screen size, app management, getprop)
still use plain ``adb`` subprocess calls.

The minitouch binary is bundled per-ABI under play_monkey/binaries and pushed
to the device at connect() time. If minitouch cannot be started, connect()
fails loudly - there is no fallback to the slow ``input`` path.
"""

import asyncio
import importlib.resources as resources
import re
import subprocess
import threading
from typing import Optional, Tuple

from .base import Device
from .minitouch import (
    DEVICE_BINARY_PATH,
    MinitouchSession,
    build_swipe_steps,
    scale,
)

# ABIs we ship a minitouch binary for, preferred order for abilist matching.
SUPPORTED_ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86")


class AndroidDevice(Device):
    """Android device using minitouch for tap/swipe injection."""

    def __init__(self, device_id: str, adb_path: str = "adb"):
        """Initialize Android device.

        Args:
            device_id: Device serial number
            adb_path: Path to ADB executable (for non-input operations)
        """
        self.device_id = device_id
        self.adb_path = adb_path
        self._connected = False
        self._screen_width: Optional[int] = None
        self._screen_height: Optional[int] = None

        # minitouch session (established once per test)
        self._minitouch: Optional[MinitouchSession] = None

        # Dedicated event loop for async socket I/O
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()

    def _run_adb_command(self, command: str, timeout: int = 10) -> Tuple[bool, str]:
        """Run an ADB command via subprocess (for non-input operations).

        Used for device state queries, screen size, app management, etc.
        Touch events go through minitouch instead.
        """
        full_command = f"{self.adb_path} -s {self.device_id} {command}"
        try:
            result = subprocess.run(
                full_command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return (result.returncode == 0, result.stdout)
        except subprocess.TimeoutExpired:
            return (False, "Command timed out")
        except Exception as e:
            return (False, str(e))

    def _start_event_loop(self) -> None:
        """Start dedicated event loop in background thread."""
        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop_ready.set()
            self._loop.run_forever()

        self._loop_thread = threading.Thread(target=run_loop, daemon=True)
        self._loop_thread.start()
        self._loop_ready.wait(timeout=5)

    def _stop_event_loop(self) -> None:
        """Stop background event loop."""
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread:
                self._loop_thread.join(timeout=2)
            self._loop = None
            self._loop_thread = None
            self._loop_ready.clear()

    def _run_async(self, coro, timeout: float = 10.0):
        """Run coroutine in the event loop and wait for result."""
        if not self._loop:
            return None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def _detect_abi(self) -> str:
        """Detect the device CPU ABI and map it to a bundled binary.

        Falls back to ro.product.cpu.abilist to find the first supported ABI.
        Returns a value from SUPPORTED_ABIS, or "" if none match.
        """
        success, output = self._run_adb_command("shell getprop ro.product.cpu.abi")
        abi = output.strip() if success else ""
        if abi in SUPPORTED_ABIS:
            return abi

        success, output = self._run_adb_command("shell getprop ro.product.cpu.abilist")
        if success:
            for candidate in output.strip().split(","):
                candidate = candidate.strip()
                if candidate in SUPPORTED_ABIS:
                    return candidate
        return ""

    def _push_minitouch(self, abi: str) -> bool:
        """Push the bundled minitouch binary for ``abi`` and make it executable."""
        binary = (
            resources.files("play_monkey")
            / "binaries"
            / "android"
            / "minitouch"
            / abi
            / "minitouch"
        )
        try:
            with resources.as_file(binary) as local_path:
                result = subprocess.run(
                    f"{self.adb_path} -s {self.device_id} push "
                    f"{local_path} {DEVICE_BINARY_PATH}",
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            if result.returncode != 0:
                print(f"Warning: minitouch push failed: {result.stderr.strip()}")
                return False
        except Exception as e:
            print(f"Warning: minitouch push failed: {e}")
            return False

        success, _ = self._run_adb_command(f"shell chmod 755 {DEVICE_BINARY_PATH}")
        return success

    def connect(self) -> bool:
        """Connect to device and start a minitouch session.

        Establishes everything once per test: detect ABI, push minitouch, start
        it, and connect to its control socket. Fails loudly if minitouch cannot
        be brought up - no fallback to the slow ``input`` path.
        """
        success, output = self._run_adb_command("get-state")
        if not (success and "device" in output):
            return False

        self._connected = True
        self._screen_width, self._screen_height = self.get_screen_size()

        abi = self._detect_abi()
        if not abi:
            print("Error: no bundled minitouch binary for this device's ABI")
            self._connected = False
            return False

        if not self._push_minitouch(abi):
            self._connected = False
            return False

        # Start background event loop for async socket I/O
        self._start_event_loop()

        try:
            session = MinitouchSession(self.device_id)
            self._run_async(session.open(), timeout=10.0)
            self._minitouch = session
        except Exception as e:
            print(f"Error: failed to start minitouch: {e}")
            self._minitouch = None
            self._stop_event_loop()
            self._connected = False
            return False

        return True

    def disconnect(self) -> None:
        """Close the minitouch session and stop the event loop."""
        if self._minitouch and self._loop:
            try:
                self._run_async(self._minitouch.close(), timeout=3.0)
            except Exception:
                pass
            self._minitouch = None

        self._stop_event_loop()
        self._connected = False

    def get_screen_size(self) -> Tuple[int, int]:
        """Get device screen dimensions."""
        if self._screen_width and self._screen_height:
            return (self._screen_width, self._screen_height)

        success, output = self._run_adb_command("shell wm size")
        if success:
            match = re.search(r"(\d+)x(\d+)", output)
            if match:
                width = int(match.group(1))
                height = int(match.group(2))
                self._screen_width = width
                self._screen_height = height
                return (width, height)

        return (1080, 1920)

    def _touch_space(self) -> Tuple[int, int]:
        """minitouch coordinate space (max_x, max_y) from the handshake banner."""
        if self._minitouch and self._minitouch.banner:
            return (self._minitouch.banner.max_x, self._minitouch.banner.max_y)
        # Fallback to display space if banner is somehow unavailable.
        return (self._screen_width or 1080, self._screen_height or 1920)

    def tap(self, x: int, y: int) -> bool:
        """Inject a tap via minitouch."""
        if not self._connected or not self._minitouch:
            return False
        max_x, max_y = self._touch_space()
        tx, ty = scale(
            x, y, self._screen_width or 1080, self._screen_height or 1920, max_x, max_y
        )
        try:
            self._run_async(self._minitouch.tap(tx, ty), timeout=2.0)
            return True
        except Exception:
            return False

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> bool:
        """Inject a swipe via minitouch, blocking for the gesture duration."""
        if not self._connected or not self._minitouch:
            return False
        max_x, max_y = self._touch_space()
        sw = self._screen_width or 1080
        sh = self._screen_height or 1920
        tx1, ty1 = scale(x1, y1, sw, sh, max_x, max_y)
        tx2, ty2 = scale(x2, y2, sw, sh, max_x, max_y)
        points, step_sleep = build_swipe_steps(tx1, ty1, tx2, ty2, duration_ms)
        try:
            self._run_async(
                self._minitouch.swipe(tx1, ty1, points, step_sleep),
                timeout=duration_ms / 1000.0 + 3.0,
            )
            return True
        except Exception:
            return False

    def is_app_running(self, app_identifier: str) -> bool:
        """Check if the specified app is running."""
        success, output = self._run_adb_command(f"shell pidof {app_identifier}")
        return success and len(output.strip()) > 0

    def start_app(self, app_identifier: str) -> bool:
        """Start the specified app."""
        success, _ = self._run_adb_command(
            f"shell monkey -p {app_identifier} -c android.intent.category.LAUNCHER 1"
        )
        return success

    def stop_app(self, app_identifier: str) -> bool:
        """Stop the specified app."""
        success, _ = self._run_adb_command(f"shell am force-stop {app_identifier}")
        return success

    def get_device_info(self) -> dict:
        """Get device information."""
        info = {
            "device_id": self.device_id,
            "platform": "android",
        }

        success, output = self._run_adb_command("shell getprop ro.build.version.release")
        if success:
            info["android_version"] = output.strip()

        success, output = self._run_adb_command("shell getprop ro.product.model")
        if success:
            info["model"] = output.strip()

        success, output = self._run_adb_command("shell getprop ro.product.manufacturer")
        if success:
            info["manufacturer"] = output.strip()

        return info
