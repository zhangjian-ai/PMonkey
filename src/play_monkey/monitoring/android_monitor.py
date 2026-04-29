"""Android performance monitoring using ADB and dumpsys."""

import re
import subprocess
from datetime import datetime
from typing import List, Optional

from .base import PerformanceMonitor
from .metrics import MetricSample


class AndroidMonitor(PerformanceMonitor):
    """Android performance monitor using ADB commands."""

    def __init__(self, device_id: str, adb_path: str = "adb"):
        """Initialize Android monitor.

        Args:
            device_id: Device serial number
            adb_path: Path to ADB executable
        """
        self.device_id = device_id
        self.adb_path = adb_path
        self.app_package: Optional[str] = None
        self.samples: List[MetricSample] = []

    def _run_adb_command(self, command: str, timeout: int = 5) -> tuple[bool, str]:
        """Run an ADB command."""
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
        except Exception:
            return (False, "")

    def start_monitoring(self, app_identifier: str) -> None:
        """Start monitoring the specified app."""
        self.app_package = app_identifier
        self.samples = []

    def collect_sample(self) -> MetricSample:
        """Collect a single performance metric sample."""
        sample = MetricSample(timestamp=datetime.now())

        if self.app_package:
            # Collect CPU
            sample.cpu_percent = self._get_cpu_usage()

            # Collect Memory
            sample.memory_mb = self._get_memory_usage()

            # Collect FPS
            sample.fps = self._get_fps()

        # Collect Battery (system-wide)
        sample.battery_percent = self._get_battery_level()

        self.samples.append(sample)
        return sample

    def stop_monitoring(self) -> List[MetricSample]:
        """Stop monitoring and return all collected samples."""
        return self.samples

    def _get_cpu_usage(self) -> Optional[float]:
        """Get CPU usage for the app."""
        if not self.app_package:
            return None

        success, output = self._run_adb_command(
            f"shell dumpsys cpuinfo | grep {self.app_package}"
        )

        if success and output:
            # Parse output like "12% 1234/com.example.app: 10% user + 2% kernel"
            match = re.search(r"(\d+(?:\.\d+)?)%", output)
            if match:
                return float(match.group(1))

        return None

    def _get_memory_usage(self) -> Optional[float]:
        """Get memory usage for the app in MB."""
        if not self.app_package:
            return None

        success, output = self._run_adb_command(
            f"shell dumpsys meminfo {self.app_package} | grep 'TOTAL'"
        )

        if success and output:
            # Parse output to get total memory in KB, convert to MB
            match = re.search(r"TOTAL\s+(\d+)", output)
            if match:
                memory_kb = int(match.group(1))
                return memory_kb / 1024.0

        return None

    def _get_fps(self) -> Optional[float]:
        """Get FPS for the app.

        Note: This is a simplified implementation. Real FPS monitoring
        requires parsing gfxinfo framestats which is more complex.
        """
        # FPS monitoring is complex and requires parsing frame stats
        # For now, return None as a placeholder
        # TODO: Implement proper FPS monitoring using gfxinfo framestats
        return None

    def _get_battery_level(self) -> Optional[float]:
        """Get battery level percentage."""
        success, output = self._run_adb_command("shell dumpsys battery | grep level")

        if success and output:
            # Parse output like "level: 85"
            match = re.search(r"level:\s*(\d+)", output)
            if match:
                return float(match.group(1))

        return None
