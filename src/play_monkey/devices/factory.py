"""Device factory for creating platform-specific device instances."""

import subprocess
from typing import List, Optional

from ..config.models import Platform
from .android import AndroidDevice
from .base import Device


class DeviceInfo:
    """Device information."""

    def __init__(self, device_id: str, platform: Platform, name: str = ""):
        self.device_id = device_id
        self.platform = platform
        self.name = name

    def __str__(self) -> str:
        return f"{self.platform.value}: {self.device_id} ({self.name})"


class DeviceFactory:
    """Factory for creating and detecting devices."""

    @staticmethod
    def create_device(device_id: str, platform: Platform, **kwargs) -> Device:
        """Create a device instance.

        Args:
            device_id: Device identifier
            platform: Target platform
            **kwargs: Additional platform-specific arguments

        Returns:
            Device instance

        Raises:
            ValueError: If platform is not supported
        """
        if platform == Platform.ANDROID:
            adb_path = kwargs.get("adb_path", "adb")
            return AndroidDevice(device_id, adb_path)
        elif platform == Platform.IOS:
            # TODO: Implement iOS device creation
            raise NotImplementedError("iOS device support not yet implemented")
        else:
            raise ValueError(f"Unsupported platform: {platform}")

    @staticmethod
    def detect_android_devices(adb_path: str = "adb") -> List[DeviceInfo]:
        """Detect connected Android devices.

        Args:
            adb_path: Path to ADB executable

        Returns:
            List of detected Android devices
        """
        devices = []

        try:
            result = subprocess.run(
                f"{adb_path} devices -l",
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")[1:]  # Skip header
                for line in lines:
                    if line.strip() and "device" in line:
                        parts = line.split()
                        device_id = parts[0]
                        # Extract device name if available
                        name = ""
                        for part in parts:
                            if part.startswith("model:"):
                                name = part.split(":")[1]
                                break

                        devices.append(DeviceInfo(device_id, Platform.ANDROID, name))

        except Exception:
            pass

        return devices

    @staticmethod
    def detect_ios_devices() -> List[DeviceInfo]:
        """Detect connected iOS devices.

        Returns:
            List of detected iOS devices
        """
        # TODO: Implement iOS device detection
        return []

    @staticmethod
    def detect_all_devices() -> List[DeviceInfo]:
        """Detect all connected devices (Android and iOS).

        Returns:
            List of all detected devices
        """
        devices = []
        devices.extend(DeviceFactory.detect_android_devices())
        devices.extend(DeviceFactory.detect_ios_devices())
        return devices
