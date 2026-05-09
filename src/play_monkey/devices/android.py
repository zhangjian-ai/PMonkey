"""Android device implementation with persistent ADB shell session.

Based on tms-agent's high-performance socket communication.
Key design: one TCP connection + one shell session per test run.
All tap/swipe commands reuse the same session.
"""

import asyncio
import re
import subprocess
import threading
from typing import Optional, Tuple

from .base import Device
from .adb_protocol import AdbClient, PersistentShellSession


class AndroidDevice(Device):
    """Android device with persistent ADB shell session for input commands."""

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

        # Persistent shell session (established once per test)
        self._shell_session: Optional[PersistentShellSession] = None

        # Dedicated event loop for async ADB operations
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()

    def _run_adb_command(self, command: str, timeout: int = 10) -> Tuple[bool, str]:
        """Run an ADB command via subprocess (for non-input operations).

        Used for device state queries, screen size, app management, etc.
        Input events go through the persistent shell session instead.
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

    def connect(self) -> bool:
        """Connect to device and establish persistent shell session.

        This is the only place where we create the ADB transport connection.
        Subsequent tap/swipe calls reuse the same session.
        """
        success, output = self._run_adb_command("get-state")
        if not (success and "device" in output):
            return False

        self._connected = True
        self._screen_width, self._screen_height = self.get_screen_size()

        # Start background event loop for async socket I/O
        self._start_event_loop()

        # Establish the ONE persistent shell session for this test
        try:
            client = AdbClient()
            self._shell_session = self._run_async(
                client.open_persistent_shell(self.device_id),
                timeout=10.0,
            )
        except Exception as e:
            # If persistent shell fails, device is still marked connected
            # but input commands will return False
            self._shell_session = None
            print(f"Warning: Failed to open persistent shell: {e}")
            return False

        return True

    def disconnect(self) -> None:
        """Close persistent shell session and stop event loop.

        Kills any queued input commands on the device by terminating running
        input processes before closing the connection.
        """
        # Kill device-side input commands that may still be executing
        self._run_adb_command("shell pkill -f 'input '")

        if self._shell_session and self._loop:
            try:
                self._run_async(self._shell_session.close(), timeout=3.0)
            except Exception:
                pass
            self._shell_session = None

        self._stop_event_loop()
        self._connected = False

    def _send_input(self, command: str) -> bool:
        """Send input command through the persistent shell session.

        This reuses the single TCP connection established at connect() time.
        No new connection, no transport switch, no shell fork - just a stdin write.
        """
        if not self._shell_session or not self._loop:
            return False

        async def send():
            return await self._shell_session.send_command(command)

        try:
            future = asyncio.run_coroutine_threadsafe(send(), self._loop)
            return future.result(timeout=1.0)
        except Exception:
            return False

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

    def tap(self, x: int, y: int) -> bool:
        """Send tap through persistent shell session."""
        if not self._connected:
            return False
        return self._send_input(f"input tap {x} {y}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> bool:
        """Send swipe through persistent shell session."""
        if not self._connected:
            return False
        return self._send_input(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")

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
