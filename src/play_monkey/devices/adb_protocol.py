"""Direct ADB protocol implementation for high-performance communication.

Using asyncio for direct TCP communication with the ADB daemon, bypassing
shell overhead. Provides the low-level wire protocol (length-prefixed commands,
OKAY/FAIL handling) and a single TCP connection abstraction. Higher-level
sessions (e.g. minitouch) build on AdbConnection by switching transport and
requesting a local service.
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
