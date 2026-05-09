"""Direct ADB protocol implementation for high-performance communication.

Based on tms-agent's implementation, using asyncio for direct TCP communication
with ADB daemon, bypassing shell overhead.

Key optimization: reuse a single persistent shell session per device,
sending all input commands through that one TCP connection.
"""

import asyncio
import socket as socket_module
from typing import Optional


class AdbProtocol:
    """ADB protocol constants and utilities."""

    OKAY = b'OKAY'
    FAIL = b'FAIL'

    @staticmethod
    def encode_command(cmd: str) -> bytes:
        """Encode command with 4-byte hex length prefix."""
        data = cmd.encode('utf-8')
        return f"{len(data):04x}".encode('ascii') + data

    @staticmethod
    def decode_length(length_str: str) -> int:
        """Decode hex length string to integer."""
        return int(length_str, 16)


class AdbConnection:
    """Direct TCP connection to ADB daemon."""

    def __init__(self, host: str = '127.0.0.1', port: int = 5037):
        self.host = host
        self.port = port
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._closed = False

    async def connect(self) -> 'AdbConnection':
        """Establish TCP connection to ADB daemon."""
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            # Enable TCP_NODELAY for low latency
            sock = self.writer.get_extra_info('socket')
            if sock:
                sock.setsockopt(socket_module.IPPROTO_TCP, socket_module.TCP_NODELAY, 1)
            return self
        except Exception as e:
            raise ConnectionError(f"Failed to connect to ADB daemon at {self.host}:{self.port}: {e}")

    async def write(self, data: bytes) -> None:
        """Write data to connection."""
        if self._closed or not self.writer:
            raise ConnectionError("Connection closed")
        self.writer.write(data)
        await self.writer.drain()

    def write_nowait(self, data: bytes) -> None:
        """Write data without awaiting drain (fastest path for fire-and-forget)."""
        if self._closed or not self.writer:
            raise ConnectionError("Connection closed")
        self.writer.write(data)

    async def read_bytes(self, n: int) -> bytes:
        """Read exactly n bytes."""
        if self._closed or not self.reader:
            raise ConnectionError("Connection closed")
        try:
            return await self.reader.readexactly(n)
        except asyncio.IncompleteReadError as e:
            raise ConnectionError(f"Connection closed: got {len(e.partial)} bytes, expected {n}")

    async def read_string(self, n: int) -> str:
        """Read n bytes and decode as UTF-8."""
        data = await self.read_bytes(n)
        return data.decode('utf-8')

    async def check_okay(self) -> bool:
        """Check for OKAY/FAIL response."""
        response = await self.read_bytes(4)
        if response == AdbProtocol.OKAY:
            return True
        elif response == AdbProtocol.FAIL:
            try:
                length_str = await self.read_string(4)
                length = AdbProtocol.decode_length(length_str)
                error_msg = await self.read_string(length)
                raise RuntimeError(f"ADB command failed: {error_msg}")
            except RuntimeError:
                raise
            except Exception:
                raise RuntimeError("ADB command failed")
        else:
            raise RuntimeError(f"Unexpected response: {response}")

    def is_alive(self) -> bool:
        """Check if connection is still usable."""
        if self._closed or not self.writer:
            return False
        return not self.writer.is_closing()

    async def close(self) -> None:
        """Close connection."""
        if self._closed:
            return
        self._closed = True
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass


class PersistentShellSession:
    """Persistent interactive shell session to a device.

    Opens a single TCP connection to ADB daemon, switches to device transport,
    then opens an interactive shell. All subsequent commands are sent through
    this one connection via stdin - no new connections per command.
    """

    def __init__(self, serial: str, host: str = '127.0.0.1', port: int = 5037):
        self.serial = serial
        self.host = host
        self.port = port
        self._conn: Optional[AdbConnection] = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open persistent shell session.

        Protocol sequence:
        1. TCP connect to ADB daemon
        2. Send 'host:transport:<serial>' to switch transport
        3. Send 'shell:' to open interactive shell
        4. Keep connection open for subsequent command writes
        """
        self._conn = AdbConnection(self.host, self.port)
        await self._conn.connect()

        # Switch to device transport
        await self._conn.write(AdbProtocol.encode_command(f"host:transport:{self.serial}"))
        await self._conn.check_okay()

        # Open interactive shell - no command means it stays open
        await self._conn.write(AdbProtocol.encode_command("shell:"))
        await self._conn.check_okay()

        # From here on, the connection is a raw stdin/stdout pipe to the shell

    async def send_command(self, command: str) -> bool:
        """Send a shell command through the persistent session.

        Appends '&' to run the command in background on the device shell,
        so it starts executing immediately without blocking subsequent commands.

        Args:
            command: Shell command to execute (e.g., "input tap 500 1000")

        Returns:
            True if command was sent, False if session is dead
        """
        if not self._conn or not self._conn.is_alive():
            return False

        try:
            self._conn.write_nowait(f"{command} &\n".encode('utf-8'))
            return True
        except Exception:
            return False

    async def flush(self) -> None:
        """Ensure pending writes are sent to the device."""
        if self._conn and self._conn.is_alive() and self._conn.writer:
            try:
                await self._conn.writer.drain()
            except Exception:
                pass

    async def close(self) -> None:
        """Close the persistent shell session."""
        if self._conn:
            try:
                # Send exit to cleanly terminate shell
                self._conn.write_nowait(b"exit\n")
            except Exception:
                pass
            await self._conn.close()
            self._conn = None


class AdbClient:
    """High-level ADB client using direct protocol."""

    def __init__(self, host: str = '127.0.0.1', port: int = 5037):
        self.host = host
        self.port = port

    async def open_persistent_shell(self, serial: str) -> PersistentShellSession:
        """Open a persistent shell session for the given device.

        This establishes the ADB transport connection ONCE and keeps it open
        for the duration of the test. All subsequent commands are sent through
        this single connection.
        """
        session = PersistentShellSession(serial, self.host, self.port)
        await session.open()
        return session
