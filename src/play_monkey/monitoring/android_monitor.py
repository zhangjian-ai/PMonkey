"""Android performance monitoring via SoloX library.

Uses SoloX (https://github.com/smart-test-ti/SoloX) for real-time performance
metrics collection. SoloX provides CPU, memory, and FPS data without requiring
device-side APK installation or floating windows.

Battery consumption is tracked via dumpsys batterystats:
- Reset stats at monitoring start
- Parse app-specific battery drain at monitoring stop
"""

import re
import subprocess
import threading
import time
from datetime import datetime
from typing import List, Optional, Dict, Any

from solox.public.apm import CPU, Memory, FPS, Battery

from .base import PerformanceMonitor
from .metrics import MetricSample


class _LatestMetrics:
    """Thread-safe holder for the most recent metrics from SoloX collectors."""

    def __init__(self):
        self._cpu: Optional[float] = None
        self._sys_cpu: Optional[float] = None
        self._memory: Optional[float] = None
        self._fps: Optional[float] = None
        self._jank: Optional[int] = None
        self._temperature: Optional[float] = None
        self._lock = threading.Lock()

    def update(
        self,
        cpu: Optional[float] = None,
        sys_cpu: Optional[float] = None,
        mem: Optional[float] = None,
        fps: Optional[float] = None,
        jank: Optional[int] = None,
        temp: Optional[float] = None,
    ):
        with self._lock:
            if cpu is not None:
                self._cpu = cpu
            if sys_cpu is not None:
                self._sys_cpu = sys_cpu
            if mem is not None:
                self._memory = mem
            if fps is not None:
                self._fps = fps
            if jank is not None:
                self._jank = jank
            if temp is not None:
                self._temperature = temp

    def get(self):
        with self._lock:
            return (
                self._cpu, self._sys_cpu,
                self._memory,
                self._fps, self._jank,
                self._temperature,
            )


class AndroidMonitor(PerformanceMonitor):
    """Android performance monitor using SoloX library.

    Collects CPU, memory, and FPS via SoloX collectors on background threads.
    Tracks app-specific battery consumption via dumpsys batterystats.
    """

    def __init__(self, device_id: str, adb_path: str = "adb", sample_interval_seconds: float = 2.0):
        self.device_id = device_id
        self.adb_path = adb_path
        self.sample_interval_seconds = sample_interval_seconds
        self.app_package: Optional[str] = None
        self.samples: List[MetricSample] = []

        self._latest = _LatestMetrics()
        self._cpu_collector: Optional[CPU] = None
        self._mem_collector: Optional[Memory] = None
        self._fps_collector: Optional[FPS] = None
        self._bat_collector: Optional[Battery] = None
        self._cpu_core_count: int = 1

        self._sampler_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self.battery_drain_mah: Optional[float] = None
        self.battery_components: Dict[str, float] = {}

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
        """Start monitoring by initializing SoloX collectors and resetting batterystats.

        Includes a warmup phase that waits until SoloX can successfully collect
        CPU data before returning, so the main test loop starts with valid metrics.
        """
        self.app_package = app_identifier
        self.samples = []
        self._stop.clear()

        # Reset batterystats to track app-specific battery consumption
        self._run_adb_command("shell dumpsys batterystats --reset", timeout=10)

        # Initialize SoloX collectors
        try:
            self._cpu_collector = CPU(
                pkgName=app_identifier,
                deviceId=self.device_id,
                platform="Android"
            )
            self._mem_collector = Memory(
                pkgName=app_identifier,
                deviceId=self.device_id,
                platform="Android"
            )
            self._fps_collector = FPS(
                pkgName=app_identifier,
                deviceId=self.device_id,
                platform="Android",
                surfaceview=True
            )
            self._bat_collector = Battery(
                deviceId=self.device_id,
                platform="Android"
            )
            # Get CPU core count for correcting SoloX calculation
            core_list = self._cpu_collector.getCpuCoreStat()
            if core_list:
                self._cpu_core_count = len(core_list)
        except Exception as e:
            print(f"Failed to initialize SoloX collectors: {e}")
            return

        # Warmup: wait until SoloX can collect valid CPU data
        self._warmup()

        # Start background sampling thread
        self._sampler_thread = threading.Thread(
            target=self._sample_loop,
            name="android-solox-sampler",
            daemon=True
        )
        self._sampler_thread.start()

    def _warmup(self, max_attempts: int = 10) -> None:
        """Wait until SoloX collectors produce valid data."""
        print("Warming up performance collectors...")
        for i in range(max_attempts):
            try:
                result = self._cpu_collector.getAndroidCpuRate(noLog=True)
                if isinstance(result, tuple) and len(result) >= 1 and result[0] > 0:
                    print(f"Collectors ready (attempt {i + 1})")
                    return
            except Exception:
                pass
            time.sleep(1)
        print("Warmup timeout, starting with available collectors")

    def collect_sample(self) -> MetricSample:
        """Collect a sample by reading latest values from background thread."""
        sample = MetricSample(timestamp=datetime.now())
        cpu, sys_cpu, mem, fps, jank, temp = self._latest.get()
        sample.cpu_percent = cpu
        sample.sys_cpu_percent = sys_cpu
        sample.memory_mb = mem
        sample.fps = fps
        sample.jank = jank
        sample.temperature = temp

        self.samples.append(sample)
        return sample

    def stop_monitoring(self) -> List[MetricSample]:
        """Stop monitoring and parse app-specific battery consumption."""
        self._stop.set()

        if self._sampler_thread and self._sampler_thread.is_alive():
            self._sampler_thread.join(timeout=3)
        self._sampler_thread = None

        # Parse batterystats for app-specific battery drain
        self._parse_battery_stats()

        return self.samples

    def _sample_loop(self) -> None:
        """Background thread: continuously collect metrics from SoloX."""
        while not self._stop.is_set():
            try:
                # Collect CPU via SoloX, corrected by core count
                cpu = None
                sys_cpu = None
                if self._cpu_collector:
                    try:
                        cpu_result = self._cpu_collector.getAndroidCpuRate(noLog=True)
                        # Returns (appCpuRate, sysCpuRate); raw values should be 0-100
                        # (fraction of total machine CPU). We multiply by core count
                        # to express as "single-core percentage" (0-100*cores).
                        # Discard samples where raw values are outside 0-100 — that
                        # means SoloX's totalDelta was bad (division artifact) and
                        # multiplying would produce insane numbers.
                        if isinstance(cpu_result, tuple) and len(cpu_result) >= 2:
                            raw_app = cpu_result[0]
                            raw_sys = cpu_result[1]
                            if isinstance(raw_app, (int, float)) and 0 < raw_app <= 100:
                                cpu = round(float(raw_app), 2)
                            if isinstance(raw_sys, (int, float)) and 0 < raw_sys <= 100:
                                sys_cpu = round(float(raw_sys), 2)
                    except Exception:
                        pass

                # Collect Memory (app PSS)
                mem = None
                if self._mem_collector:
                    try:
                        mem_result = self._mem_collector.getAndroidMemory()
                        # Returns (totalPSS_MB, swapPSS_MB)
                        if isinstance(mem_result, tuple) and len(mem_result) >= 1:
                            total_pss = mem_result[0]
                            if isinstance(total_pss, (int, float)) and total_pss > 0:
                                mem = round(float(total_pss), 2)
                    except Exception:
                        pass

                # Collect FPS + Jank
                fps = None
                jank = None
                if self._fps_collector:
                    try:
                        fps_result = self._fps_collector.getAndroidFps(noLog=True)
                        # Returns (fps, jank)
                        if isinstance(fps_result, tuple) and len(fps_result) >= 2:
                            fps_val = fps_result[0]
                            jank_val = fps_result[1]
                            if isinstance(fps_val, (int, float)) and 0.1 <= fps_val <= 240:
                                fps = round(float(fps_val), 1)
                            if isinstance(jank_val, int) and jank_val >= 0:
                                jank = jank_val
                    except Exception:
                        pass

                # Collect Temperature only (battery level is meaningless when USB connected)
                temp = None
                if self._bat_collector:
                    try:
                        bat_result = self._bat_collector.getAndroidBattery(noLog=True)
                        # Returns (level, temperature_celsius)
                        if isinstance(bat_result, tuple) and len(bat_result) >= 2:
                            temperature = bat_result[1]
                            if isinstance(temperature, (int, float)) and temperature > 0:
                                temp = round(float(temperature), 1)
                    except Exception:
                        pass

                # Update latest metrics
                self._latest.update(
                    cpu=cpu, sys_cpu=sys_cpu,
                    mem=mem, fps=fps, jank=jank,
                    temp=temp,
                )

                # Sleep according to configured sample interval
                self._stop.wait(self.sample_interval_seconds)

            except Exception:
                # Sampling error - wait the configured interval before retry
                self._stop.wait(self.sample_interval_seconds)

    def _parse_battery_stats(self) -> None:
        """Parse dumpsys batterystats for app-specific battery consumption."""
        if not self.app_package:
            return

        success, output = self._run_adb_command(
            f"shell dumpsys batterystats {self.app_package}",
            timeout=15
        )
        if not success or not output:
            return

        # Extract "Computed drain" line for total mAh
        # Example: "Computed drain: 12.34 mAh"
        drain_match = re.search(r"Computed drain:\s*([\d.]+)\s*mAh", output, re.IGNORECASE)
        if drain_match:
            self.battery_drain_mah = float(drain_match.group(1))

        # Extract component breakdown
        # Example lines:
        #   CPU: 5.67 mAh
        #   Screen: 3.21 mAh
        #   Wifi: 1.23 mAh
        component_pattern = r"^\s*(CPU|Screen|Wifi|Bluetooth|Camera|Flashlight|Audio|Video|Sensor):\s*([\d.]+)\s*mAh"
        for line in output.splitlines():
            match = re.match(component_pattern, line.strip(), re.IGNORECASE)
            if match:
                component = match.group(1).capitalize()
                value = float(match.group(2))
                self.battery_components[component] = value
