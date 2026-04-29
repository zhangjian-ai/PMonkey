"""Android device implementation using ADB."""

import re
import subprocess
from typing import Optional, Tuple

from .base import Device


class AndroidDevice(Device):
    """Android device implementation using ADB commands."""

    def __init__(self, device_id: str, adb_path: str = "adb"):
        """Initialize Android device.

        Args:
            device_id: Device serial number
            adb_path: Path to ADB executable (default: "adb" from PATH)
        """
        self.device_id = device_id
        self.adb_path = adb_path
        self._connected = False
        self._screen_width: Optional[int] = None
        self._screen_height: Optional[int] = None

    def _run_adb_command(self, command: str, timeout: int = 10) -> Tuple[bool, str]:
        """Run an ADB command.

        Args:
            command: ADB command to run (without 'adb -s <device>')
            timeout: Command timeout in seconds

        Returns:
            Tuple of (success, output)
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

    def connect(self) -> bool:
        """Connect to the Android device."""
        # Check if device is available
        success, output = self._run_adb_command("get-state")
        if success and "device" in output:
            self._connected = True
            # Cache screen size
            self._screen_width, self._screen_height = self.get_screen_size()
            return True
        return False

    def disconnect(self) -> None:
        """Disconnect from the device."""
        self._connected = False

    def get_screen_size(self) -> Tuple[int, int]:
        """Get device screen dimensions.

        Returns:
            Tuple of (width, height) in pixels
        """
        if self._screen_width and self._screen_height:
            return (self._screen_width, self._screen_height)

        success, output = self._run_adb_command("shell wm size")
        if success:
            # Parse output like "Physical size: 1080x1920"
            match = re.search(r"(\d+)x(\d+)", output)
            if match:
                width = int(match.group(1))
                height = int(match.group(2))
                self._screen_width = width
                self._screen_height = height
                return (width, height)

        # Default fallback
        return (1080, 1920)

    def tap(self, x: int, y: int) -> bool:
        """Perform a tap at the specified coordinates."""
        success, _ = self._run_adb_command(f"shell input tap {x} {y}")
        return success

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> bool:
        """Perform a swipe from one point to another."""
        success, _ = self._run_adb_command(
            f"shell input swipe {x1} {y1} {x2} {y2} {duration_ms}"
        )
        return success

    def is_app_running(self, app_identifier: str) -> bool:
        """Check if the specified app is running."""
        success, output = self._run_adb_command(
            f"shell pidof {app_identifier}"
        )
        return success and len(output.strip()) > 0

    def start_app(self, app_identifier: str) -> bool:
        """Start the specified app.

        Args:
            app_identifier: App package name

        Returns:
            True if app started successfully
        """
        # Use monkey command to start the app
        success, _ = self._run_adb_command(
            f"shell monkey -p {app_identifier} -c android.intent.category.LAUNCHER 1"
        )
        return success

    def stop_app(self, app_identifier: str) -> bool:
        """Stop the specified app.

        Args:
            app_identifier: App package name

        Returns:
            True if app stopped successfully
        """
        success, _ = self._run_adb_command(
            f"shell am force-stop {app_identifier}"
        )
        return success

    def get_device_info(self) -> dict:
        """Get device information.

        Returns:
            Dictionary with device information
        """
        info = {
            "device_id": self.device_id,
            "platform": "android",
        }

        # Get Android version
        success, output = self._run_adb_command("shell getprop ro.build.version.release")
        if success:
            info["android_version"] = output.strip()

        # Get device model
        success, output = self._run_adb_command("shell getprop ro.product.model")
        if success:
            info["model"] = output.strip()

        # Get manufacturer
        success, output = self._run_adb_command("shell getprop ro.product.manufacturer")
        if success:
            info["manufacturer"] = output.strip()

        return info
