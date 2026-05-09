"""Device factory for creating platform-specific device instances."""

import re
import subprocess
from typing import List, Optional

from ..config.models import Platform
from .android import AndroidDevice
from .base import Device
from .ios import IOSDevice


class DeviceInfo:
    """Device information."""

    def __init__(
        self,
        device_id: str,
        platform: Platform,
        name: str = "",
        os_version: str = "",
        resolution: str = "",
    ):
        self.device_id = device_id
        self.platform = platform
        self.name = name
        self.os_version = os_version
        self.resolution = resolution

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
                - adb_path: Path to ADB executable (Android)
                - force_backend: Force specific iOS backend ('tidevice' or 'tidevice3')
                                 If not specified, backend is auto-selected based on iOS version

        Returns:
            Device instance

        Raises:
            ValueError: If platform is not supported
        """
        if platform == Platform.ANDROID:
            adb_path = kwargs.get("adb_path", "adb")
            return AndroidDevice(device_id, adb_path)
        elif platform == Platform.IOS:
            force_backend = kwargs.get("force_backend", None)
            return IOSDevice(device_id, force_backend=force_backend)
        else:
            raise ValueError(f"Unsupported platform: {platform}")

    @staticmethod
    def _adb_getprop(adb_path: str, device_id: str, prop: str) -> str:
        """Read a single ro.* property from a connected Android device.

        Returns "" on any failure so callers can keep going - device
        listing is best-effort, missing fields just don't show up in
        the printout.
        """
        try:
            result = subprocess.run(
                f"{adb_path} -s {device_id} shell getprop {prop}",
                shell=True, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _adb_resolution(adb_path: str, device_id: str) -> str:
        """Read screen size via `wm size`. Returns "" on failure."""
        try:
            result = subprocess.run(
                f"{adb_path} -s {device_id} shell wm size",
                shell=True, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                # Output looks like "Physical size: 1080x2400" possibly
                # followed by an "Override size:" line.
                m = re.search(r"(\d+)x(\d+)", result.stdout)
                if m:
                    return f"{m.group(1)}x{m.group(2)}"
        except Exception:
            pass
        return ""

    @staticmethod
    def detect_android_devices(adb_path: str = "adb") -> List[DeviceInfo]:
        """Detect connected Android devices.

        For each detected device we additionally fetch the OS version
        and screen resolution via adb. These are best-effort - any
        device that times out simply gets blanks for those fields.
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
                    if not line.strip() or "device" not in line:
                        continue
                    parts = line.split()
                    device_id = parts[0]
                    name = ""
                    for part in parts:
                        if part.startswith("model:"):
                            name = part.split(":", 1)[1]
                            break

                    os_version = DeviceFactory._adb_getprop(
                        adb_path, device_id, "ro.build.version.release"
                    )
                    resolution = DeviceFactory._adb_resolution(adb_path, device_id)

                    devices.append(DeviceInfo(
                        device_id, Platform.ANDROID,
                        name=name,
                        os_version=os_version,
                        resolution=resolution,
                    ))

        except Exception:
            pass

        return devices

    @staticmethod
    def detect_ios_devices() -> List[DeviceInfo]:
        """Detect connected iOS devices using tidevice.

        Pulls device name, iOS version, and physical screen resolution
        from lockdown so the listing has the same depth of info as the
        Android side. Each field is fetched defensively; a single
        failing call doesn't drop the device from the list.
        """
        devices = []

        try:
            import tidevice
            for d in tidevice.Usbmux().device_list():
                udid = d.udid
                name = ""
                os_version = ""
                resolution = ""

                try:
                    td = tidevice.Device(udid)

                    try:
                        os_version = td.get_value("ProductVersion") or ""
                    except Exception:
                        pass

                    try:
                        name = td.get_value("DeviceName") or ""
                    except Exception:
                        pass

                    # Show logical coordinates (window_size / points),
                    # not physical pixels. The monkey bounds and touch
                    # coordinates are all expressed in this logical
                    # space, so this is the value users actually need
                    # when writing config files.
                    try:
                        screen = td.screen_info()
                        width = getattr(screen, "width", None)
                        height = getattr(screen, "height", None)
                        scale = getattr(screen, "scale", None)
                        if width and height:
                            if scale and scale > 0:
                                logical_w = int(round(width / scale))
                                logical_h = int(round(height / scale))
                                resolution = f"{logical_w}x{logical_h}"
                            else:
                                resolution = f"{width}x{height}"
                    except Exception:
                        # Fallback to physical pixels from lockdown if
                        # screen_info isn't available.
                        try:
                            itunes = td.get_value(domain="com.apple.mobile.iTunes")
                            if isinstance(itunes, dict):
                                w = itunes.get("ScreenWidth")
                                h = itunes.get("ScreenHeight")
                                if w and h:
                                    resolution = f"{w}x{h}"
                        except Exception:
                            pass
                except Exception:
                    pass

                devices.append(DeviceInfo(
                    udid, Platform.IOS,
                    name=name,
                    os_version=os_version,
                    resolution=resolution,
                ))
        except ImportError:
            # Fallback to pymobiledevice3 CLI - we can only get UDIDs
            # cheaply this way, so OS version / resolution stay blank.
            try:
                result = subprocess.run(
                    "pymobiledevice3 usbmux list",
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split("\n")
                    for line in lines:
                        if line.strip() and len(line) > 20:
                            parts = line.split()
                            for part in parts:
                                if len(part) >= 24 and all(
                                    c in "0123456789abcdefABCDEF-" for c in part
                                ):
                                    devices.append(DeviceInfo(part, Platform.IOS))
                                    break
            except Exception:
                pass

        return devices

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
