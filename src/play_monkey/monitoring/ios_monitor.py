"""iOS performance monitoring with automatic backend selection.

Supports two backends:
- tidevice: For iOS 16 and below (uses DTX instruments)
- pymobiledevice3: For iOS 17+ (uses basic services)

The backend is automatically selected based on iOS version to avoid
DeviceSupport compatibility issues.

Monitoring capabilities:
- iOS 16-: Full monitoring (CPU, Memory, FPS, Battery, Temperature)
- iOS 17+: Basic monitoring (Battery, Temperature) via pymobiledevice3

Prerequisites:
- Device must be connected via USB
- Device must be in developer mode
- For iOS 17+: pymobiledevice3 must be installed
"""

import threading
import time
from datetime import datetime
from typing import List, Optional, Dict, Any

from .base import PerformanceMonitor
from .metrics import MetricSample


class _LatestValue:
    """Thread-safe single-slot holder for the most recent metric value."""

    def __init__(self):
        self._value: Optional[float] = None
        self._lock = threading.Lock()

    def set(self, v: Optional[float]) -> None:
        with self._lock:
            self._value = v

    def get(self) -> Optional[float]:
        with self._lock:
            return self._value


class IOSMonitor(PerformanceMonitor):
    """iOS performance monitor with automatic backend selection.

    Uses tidevice for iOS 16 and below, pymobiledevice3 for iOS 17+.
    """

    def __init__(self, device_id: str, sample_interval_seconds: float = 2.0):
        self.device_id = device_id
        self.sample_interval_seconds = sample_interval_seconds
        self.app_package: Optional[str] = None
        self.samples: List[MetricSample] = []

        self._backend: Optional[str] = None  # 'tidevice' or 'pymobiledevice3'
        self._td_device = None  # tidevice.Device
        self._pmd3_lockdown = None  # pymobiledevice3 lockdown client (battery)
        self._pmd3_rsd = None  # RemoteServiceDiscoveryService (DVT)
        self._app_pid: Optional[int] = None

        self._cpu_latest = _LatestValue()
        self._mem_latest = _LatestValue()
        self._fps_latest = _LatestValue()
        self._temp_latest = _LatestValue()

        self._cpu_mem_service = None
        self._fps_service = None
        self._cpu_mem_thread: Optional[threading.Thread] = None
        self._fps_thread: Optional[threading.Thread] = None
        self._power_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Battery consumption tracking (device-level)
        self.battery_drain_mah: Optional[float] = None
        self.battery_components: Dict[str, float] = {}
        self._power_samples: List[tuple] = []  # (timestamp, power_mw, voltage_mv)

    # ── PerformanceMonitor interface ────────────────────────────────

    def start_monitoring(self, app_identifier: str) -> None:
        self.app_package = app_identifier
        self.samples = []
        self._stop.clear()
        self._power_samples = []

        # Detect iOS version and select backend
        ios_version = self._detect_ios_version()
        if ios_version is None:
            print("iOS monitor: failed to detect iOS version")
            return

        print(f"iOS monitor: detected iOS {ios_version}")

        # Select backend based on iOS version
        if ios_version >= 17.0:
            self._backend = 'pymobiledevice3'
            success = self._init_pymobiledevice3_backend()
        else:
            self._backend = 'tidevice'
            success = self._init_tidevice_backend()

        if not success:
            print(f"iOS monitor: failed to initialize {self._backend} backend")
            return

        # Start monitoring threads based on backend
        if self._backend == 'tidevice':
            # Full monitoring with tidevice
            self._app_pid = self._resolve_pid_tidevice(app_identifier)
            if self._app_pid is None:
                print(f"iOS monitor: app '{app_identifier}' is not running")
                print("Please launch the app before starting the test")

            # Start CPU/Memory monitoring
            self._cpu_mem_thread = threading.Thread(
                target=self._consume_cpu_memory_tidevice, name="ios-cpu-mem", daemon=True
            )
            self._cpu_mem_thread.start()

            # Start FPS monitoring
            self._fps_thread = threading.Thread(
                target=self._consume_opengl_tidevice, name="ios-fps", daemon=True
            )
            self._fps_thread.start()

            # Start power monitoring
            self._power_thread = threading.Thread(
                target=self._sample_power_tidevice, name="ios-power", daemon=True
            )
            self._power_thread.start()

        else:  # pymobiledevice3
            print(f"iOS monitor: iOS {ios_version} - using pymobiledevice3 backend")

            # Battery / temperature via plain lockdown - always available.
            self._power_thread = threading.Thread(
                target=self._sample_power_pymobiledevice3, name="ios-power", daemon=True
            )
            self._power_thread.start()

            # CPU / Memory / FPS via DVT on RSD - requires tunneld. If
            # RSD didn't come up we skip these, matching the old
            # "battery-only" behavior rather than crashing.
            if self._pmd3_rsd is not None:
                self._app_pid = self._resolve_pid_pymobiledevice3(app_identifier)
                if self._app_pid is None:
                    print(
                        f"iOS monitor: app '{app_identifier}' PID not yet resolved; "
                        "will retry on first CPU/Memory sample"
                    )

                self._cpu_mem_thread = threading.Thread(
                    target=self._consume_cpu_memory_pymobiledevice3,
                    name="ios-cpu-mem", daemon=True,
                )
                self._cpu_mem_thread.start()

                self._fps_thread = threading.Thread(
                    target=self._consume_fps_pymobiledevice3,
                    name="ios-fps", daemon=True,
                )
                self._fps_thread.start()

        print("iOS monitor: performance monitoring started")

    def collect_sample(self) -> MetricSample:
        sample = MetricSample(timestamp=datetime.now())
        sample.cpu_percent = self._cpu_latest.get()
        sample.memory_mb = self._mem_latest.get()
        sample.fps = self._fps_latest.get()
        sample.temperature = self._temp_latest.get()
        self.samples.append(sample)
        return sample

    def stop_monitoring(self) -> List[MetricSample]:
        # Signal all threads to stop
        self._stop.set()

        # Wait for threads to finish before closing services
        for t in (self._cpu_mem_thread, self._fps_thread, self._power_thread):
            if t and t.is_alive():
                t.join(timeout=3)

        # Close services based on backend
        if self._backend == 'tidevice':
            for svc_attr in ("_cpu_mem_service", "_fps_service"):
                svc = getattr(self, svc_attr, None)
                if svc is not None:
                    try:
                        svc.close()
                    except Exception:
                        pass
                    setattr(self, svc_attr, None)
        elif self._backend == 'pymobiledevice3':
            if self._pmd3_rsd is not None:
                try:
                    from pymobiledevice3.utils import get_asyncio_loop
                    get_asyncio_loop().run_until_complete(self._pmd3_rsd.close())
                except Exception:
                    pass
                self._pmd3_rsd = None
            if self._pmd3_lockdown:
                try:
                    self._pmd3_lockdown.close()
                except Exception:
                    pass
                self._pmd3_lockdown = None

        # Clean up thread references
        self._cpu_mem_thread = None
        self._fps_thread = None
        self._power_thread = None

        # Calculate total battery consumption from power samples
        self._calculate_battery_drain()

        return self.samples

    # ── Backend initialization ──────────────────────────────────────

    def _detect_ios_version(self) -> Optional[float]:
        """Detect iOS version using tidevice first, fallback to pymobiledevice3."""
        # Try tidevice first (works for all iOS versions)
        try:
            import tidevice
            td = tidevice.Device(self.device_id)
            device_info = td.device_info()
            build_version = device_info.get("BuildVersion", "")

            # Map BuildVersion to iOS version
            if build_version.startswith("23"):
                return 18.1
            elif build_version.startswith("22"):
                return 18.0
            elif build_version.startswith("21"):
                return 17.0
            elif build_version.startswith("20"):
                return 16.0
            else:
                # Try to parse ProductVersion
                product_version = device_info.get("ProductVersion", "16.0")
                try:
                    version = float(product_version)
                    if version < 20:
                        return version
                except ValueError:
                    pass
                return 16.0
        except Exception as e:
            print(f"iOS monitor: tidevice detection failed: {e}")

        # Fallback to pymobiledevice3
        try:
            from pymobiledevice3.lockdown import create_using_usbmux
            lockdown = create_using_usbmux(serial=self.device_id)
            version_str = lockdown.product_version
            # Parse version string (e.g., "18.7.7" -> 18.7)
            parts = version_str.split('.')
            if len(parts) >= 2:
                return float(f"{parts[0]}.{parts[1]}")
            return float(parts[0])
        except Exception as e:
            print(f"iOS monitor: pymobiledevice3 detection failed: {e}")

        return None

    def _init_tidevice_backend(self) -> bool:
        """Initialize tidevice backend for iOS 16 and below."""
        try:
            import tidevice
            self._td_device = tidevice.Device(self.device_id)

            # Check if device is ready
            try:
                device_info = self._td_device.info
                print(f"iOS monitor: connected via tidevice to {device_info.udid}")
                return True
            except Exception as e:
                print(f"iOS monitor: device not ready - {e}")
                print("Please ensure:")
                print("  1. Device is connected via USB")
                print("  2. Device is unlocked and trusted this computer")
                print("  3. Device is in developer mode")
                return False

        except Exception as e:
            print(f"iOS monitor: failed to create tidevice instance: {e}")
            return False

    def _init_pymobiledevice3_backend(self) -> bool:
        """Initialize pymobiledevice3 backend for iOS 17+.

        Two things get opened:
        - lockdown client: used by the battery/temperature sampler via
          the plain `com.apple.mobile.battery` domain.
        - RemoteServiceDiscoveryService (RSD): used by the DVT samplers
          for CPU/memory (Sysmontap) and FPS (Graphics). RSD needs a
          tunnel established by `t3 tunneld` / `pymobiledevice3 lockdown
          start-tunnel`; tunneld publishes per-device tunnel addresses
          at http://localhost:5555 or :49151. If tunneld isn't running
          DVT will be unavailable but the battery path still works.
        """
        try:
            from pymobiledevice3.lockdown import create_using_usbmux
            self._pmd3_lockdown = create_using_usbmux(serial=self.device_id)
            print(f"iOS monitor: connected via pymobiledevice3 to {self._pmd3_lockdown.identifier}")
        except Exception as e:
            print(f"iOS monitor: failed to create pymobiledevice3 lockdown: {e}")
            return False

        # Try to bring up RSD for DVT-based metrics. Optional - failure
        # here just means CPU/Memory/FPS won't be collected, which is
        # the behavior we had before.
        self._pmd3_rsd = self._connect_rsd_via_tunneld()
        if self._pmd3_rsd is None:
            print(
                "iOS monitor: tunneld not available - CPU/Memory/FPS "
                "won't be collected. Start it with `sudo t3 tunneld` or "
                "`pymobiledevice3 lockdown start-tunnel --script-mode "
                f"--udid {self.device_id}`."
            )
        else:
            print(f"iOS monitor: RSD connected via tunneld for DVT instruments")

        return True

    def _connect_rsd_via_tunneld(self):
        """Look up this device's tunnel address from tunneld and open RSD.

        Returns a RemoteServiceDiscoveryService if tunneld has a tunnel
        for this device, None otherwise.
        """
        try:
            import requests
        except Exception:
            return None

        tunnel_addr = None
        for port in (49151, 5555):
            try:
                resp = requests.get(f"http://localhost:{port}", timeout=2)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if self.device_id in data:
                    tunnel_addr = data[self.device_id]
                    break
            except Exception:
                continue

        if not tunnel_addr:
            return None

        try:
            from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
            from pymobiledevice3.utils import get_asyncio_loop

            if isinstance(tunnel_addr, (list, tuple)) and len(tunnel_addr) >= 2:
                host = str(tunnel_addr[0])
                port = int(tunnel_addr[1])
            else:
                return None

            rsd = RemoteServiceDiscoveryService((host, port))
            get_asyncio_loop().run_until_complete(rsd.connect())
            return rsd
        except Exception as e:
            print(f"iOS monitor: RSD connect failed: {e}")
            return None

    # ── tidevice backend methods ────────────────────────────────────

    def _resolve_pid_tidevice(self, bundle_id: str) -> Optional[int]:
        """Resolve app PID using tidevice instruments."""
        if self._td_device is None:
            return None
        try:
            with self._td_device.connect_instruments() as ins:
                # Get all running processes
                processes = ins.app_running_processes()

                # Search for the app by bundle ID or app name
                # Extract potential app name from bundle ID (e.g., "com.merge.foodie.cooking.restaurant" -> "merge", "foodie", "restaurant")
                bundle_parts = bundle_id.lower().split('.')
                search_keywords = [part for part in bundle_parts if len(part) > 3]  # Skip short parts like "com"

                for proc in processes:
                    if isinstance(proc, dict):
                        # Check if this is the app we're looking for
                        proc_name = proc.get('name', '').lower()
                        real_name = proc.get('realAppName', '').lower()
                        is_app = proc.get('isApplication', False)

                        # Match by exact bundle ID in realAppName
                        if bundle_id.lower() in real_name:
                            pid = proc.get('pid')
                            if pid:
                                print(f"iOS monitor: found app PID: {pid} for {bundle_id} (matched by bundle ID)")
                                return int(pid)

                        # Match by app name keywords (for apps like "MergeFoodie" from "com.merge.foodie.cooking.restaurant")
                        if is_app and any(keyword in proc_name or keyword in real_name for keyword in search_keywords):
                            pid = proc.get('pid')
                            if pid:
                                print(f"iOS monitor: found app PID: {pid} for {bundle_id} (matched by name: {proc.get('name')})")
                                return int(pid)

                print(f"iOS monitor: app not running: {bundle_id}")
                return None

        except Exception as e:
            print(f"iOS monitor: failed to resolve PID: {e}")
        return None

    def _consume_cpu_memory_tidevice(self) -> None:
        """Consume CPU/memory stream using tidevice."""
        if self._td_device is None:
            return
        try:
            self._cpu_mem_service = self._td_device.connect_instruments()
        except Exception as e:
            print(f"iOS monitor: failed to connect instruments for CPU/memory: {e}")
            return

        try:
            for entry in self._cpu_mem_service.iter_cpu_memory():
                if self._stop.is_set():
                    break
                self._handle_cpu_mem_entry(entry)
        except Exception as e:
            if not self._stop.is_set():
                print(f"iOS monitor: CPU/memory stream error: {e}")
        finally:
            if self._cpu_mem_service:
                try:
                    self._cpu_mem_service.close()
                except Exception:
                    pass

    def _handle_cpu_mem_entry(self, entry) -> None:
        """Extract CPU and memory from tidevice entry.

        In tidevice 0.12.10, entry is a list [system_info, processes_info]
        where processes_info is a dict containing 'Processes' key.
        """
        if self._app_pid is None:
            self._app_pid = self._resolve_pid_tidevice(self.app_package or "")
            if self._app_pid is None:
                return

        # Handle both list and dict entry formats (for compatibility)
        if isinstance(entry, list) and len(entry) >= 2:
            processes_info = entry[1]
        elif isinstance(entry, dict):
            processes_info = entry
        else:
            return

        if not isinstance(processes_info, dict):
            return

        processes = processes_info.get("Processes") or {}
        row = processes.get(self._app_pid)
        if row is None:
            return

        try:
            # Row format: [?, cpu_usage, ?, ?, memory_resident, ?, ?, pid]
            cpu_usage = float(row[1])
            mem_resident = float(row[4])
        except (IndexError, TypeError, ValueError):
            return

        self._cpu_latest.set(round(cpu_usage, 2))
        self._mem_latest.set(round(mem_resident / (1024 * 1024), 2))

    def _consume_opengl_tidevice(self) -> None:
        """Consume FPS stream using tidevice."""
        if self._td_device is None:
            return
        try:
            self._fps_service = self._td_device.connect_instruments()
        except Exception as e:
            print(f"iOS monitor: failed to connect instruments for FPS: {e}")
            return

        try:
            for entry in self._fps_service.iter_opengl_data():
                if self._stop.is_set():
                    break
                fps = entry.get("CoreAnimationFramesPerSecond")
                if isinstance(fps, (int, float)) and 0 <= fps <= 240:
                    self._fps_latest.set(float(fps))
        except Exception as e:
            if not self._stop.is_set():
                print(f"iOS monitor: FPS stream error: {e}")
        finally:
            if self._fps_service:
                try:
                    self._fps_service.close()
                except Exception:
                    pass

    def _sample_power_tidevice(self) -> None:
        """Sample power using tidevice."""
        td_power = None
        try:
            import tidevice
            td_power = tidevice.Device(self.device_id)
        except Exception as e:
            print(f"iOS power monitor: failed to create device instance: {e}")
            return

        while not self._stop.is_set():
            try:
                io_power = td_power.get_io_power()
                diagnostics = io_power.get("Diagnostics", {})
                registry = diagnostics.get("IORegistry", {})

                temperature = registry.get("Temperature")
                current_ma = abs(registry.get("InstantAmperage", 0))
                voltage_mv = registry.get("Voltage", 0)

                if temperature is not None:
                    temp_celsius = temperature / 100.0
                    if 0 < temp_celsius < 100:
                        self._temp_latest.set(round(temp_celsius, 1))

                if current_ma > 0 and voltage_mv > 0:
                    power_mw = (current_ma * voltage_mv) / 1000.0
                    timestamp = time.time()
                    self._power_samples.append((timestamp, power_mw, voltage_mv))

            except Exception as e:
                if not self._stop.is_set():
                    print(f"iOS power sampling error: {e}")
                self._stop.wait(self.sample_interval_seconds)
                continue

            self._stop.wait(self.sample_interval_seconds)

        td_power = None

    # ── pymobiledevice3 backend methods ─────────────────────────────

    def _resolve_pid_pymobiledevice3(self, bundle_id: str) -> Optional[int]:
        """Resolve app PID via DVT DeviceInfo.proclist().

        proclist() returns one dict per running process with
        bundleIdentifier, pid, name, isApplication, startDate. When the
        bundle id matches exactly we're done; otherwise we fall back to
        keyword-matching the process name, same heuristic the tidevice
        path uses (handles apps whose executable is e.g. "MergeFoodie"
        for bundle "com.merge.foodie.cooking.restaurant").
        """
        if self._pmd3_rsd is None or not bundle_id:
            return None

        try:
            from pymobiledevice3.services.dvt.dvt_secure_socket_proxy import DvtSecureSocketProxyService
            from pymobiledevice3.services.dvt.instruments.device_info import DeviceInfo

            with DvtSecureSocketProxyService(self._pmd3_rsd) as dvt:
                procs = DeviceInfo(dvt).proclist()
        except Exception as e:
            print(f"iOS monitor (pmd3): proclist failed: {e}")
            return None

        # Exact bundle-id match first
        for p in procs:
            if p.get('bundleIdentifier') == bundle_id:
                pid = p.get('pid')
                if pid:
                    return int(pid)

        # Keyword fallback: parts of bundle id inside the process name
        keywords = [part for part in bundle_id.lower().split('.') if len(part) > 3]
        for p in procs:
            if not p.get('isApplication'):
                continue
            name = (p.get('name') or '').lower()
            real = (p.get('realAppName') or '').lower()
            if any(k in name or k in real for k in keywords):
                pid = p.get('pid')
                if pid:
                    return int(pid)

        return None

    def _consume_cpu_memory_pymobiledevice3(self) -> None:
        """Stream CPU + memory for the app via DVT Sysmontap."""
        if self._pmd3_rsd is None:
            return

        try:
            from pymobiledevice3.services.dvt.dvt_secure_socket_proxy import DvtSecureSocketProxyService
            from pymobiledevice3.services.dvt.instruments.sysmontap import Sysmontap
        except Exception as e:
            print(f"iOS monitor (pmd3): failed to import DVT modules: {e}")
            return

        try:
            with DvtSecureSocketProxyService(self._pmd3_rsd) as dvt:
                sysmon = Sysmontap(dvt)
                with sysmon:
                    for row in sysmon:
                        if self._stop.is_set():
                            break
                        self._handle_sysmon_row(sysmon, row)
        except Exception as e:
            if not self._stop.is_set():
                print(f"iOS monitor (pmd3): sysmon stream error: {e}")

    def _handle_sysmon_row(self, sysmon, row) -> None:
        """Extract the target app's CPU/memory from a Sysmontap row.

        Sysmontap's `__iter__` does `yield from receive_plist()`, which
        means we can see a mix of (a) system summary dicts, (b) process
        dicts, and (c) occasional bare strings / control frames that
        the plist layer surfaces. Anything we can't interpret as a
        process dict we silently skip so the stream keeps flowing.
        """
        if not isinstance(row, dict):
            return

        processes = row.get('Processes')
        if not isinstance(processes, dict):
            return

        # Resolve PID lazily if the app wasn't running at start
        if self._app_pid is None:
            self._app_pid = self._resolve_pid_pymobiledevice3(self.app_package or "")
            if self._app_pid is None:
                return

        raw = processes.get(self._app_pid)
        if raw is None:
            # App may have died and been restarted - try to rediscover.
            self._app_pid = self._resolve_pid_pymobiledevice3(self.app_package or "")
            return

        try:
            entry = sysmon.process_attributes_cls(*raw)
        except Exception:
            return

        # cpuUsage is a percentage (e.g. 41.79 means 41.79%). Clamp
        # anything obviously bogus.
        cpu = getattr(entry, 'cpuUsage', None)
        if isinstance(cpu, (int, float)) and 0.0 <= cpu <= 1000.0:
            self._cpu_latest.set(round(float(cpu), 2))

        # Apple-recommended memory number for app footprint.
        mem_bytes = getattr(entry, 'physFootprint', None)
        if isinstance(mem_bytes, (int, float)) and mem_bytes > 0:
            self._mem_latest.set(round(mem_bytes / (1024 * 1024), 2))

    def _consume_fps_pymobiledevice3(self) -> None:
        """Stream CoreAnimation FPS via DVT Graphics."""
        if self._pmd3_rsd is None:
            return

        try:
            from pymobiledevice3.services.dvt.dvt_secure_socket_proxy import DvtSecureSocketProxyService
            from pymobiledevice3.services.dvt.instruments.graphics import Graphics
        except Exception as e:
            print(f"iOS monitor (pmd3): failed to import DVT modules: {e}")
            return

        try:
            with DvtSecureSocketProxyService(self._pmd3_rsd) as dvt:
                with Graphics(dvt) as gfx:
                    for row in gfx:
                        if self._stop.is_set():
                            break
                        fps = row.get('CoreAnimationFramesPerSecond')
                        if isinstance(fps, (int, float)) and 0 <= fps <= 240:
                            self._fps_latest.set(float(fps))
        except Exception as e:
            if not self._stop.is_set():
                print(f"iOS monitor (pmd3): graphics stream error: {e}")

    def _sample_power_pymobiledevice3(self) -> None:
        """Sample power using pymobiledevice3."""
        if self._pmd3_lockdown is None:
            return

        while not self._stop.is_set():
            try:
                # Get battery info
                battery_info = self._pmd3_lockdown.get_value(domain='com.apple.mobile.battery')
                if battery_info:
                    # Extract temperature (in Celsius * 100)
                    temp_raw = battery_info.get('Temperature')
                    if temp_raw is not None:
                        temp_celsius = temp_raw / 100.0
                        if 0 < temp_celsius < 100:
                            self._temp_latest.set(round(temp_celsius, 1))

                    # Extract power metrics
                    current_ma = abs(battery_info.get('InstantAmperage', 0))
                    voltage_mv = battery_info.get('Voltage', 0)

                    if current_ma > 0 and voltage_mv > 0:
                        power_mw = (current_ma * voltage_mv) / 1000.0
                        timestamp = time.time()
                        self._power_samples.append((timestamp, power_mw, voltage_mv))

            except Exception as e:
                if not self._stop.is_set():
                    print(f"iOS power sampling error (pymobiledevice3): {e}")
                self._stop.wait(self.sample_interval_seconds)
                continue

            self._stop.wait(self.sample_interval_seconds)

    # ── Common methods ──────────────────────────────────────────────

    def _calculate_battery_drain(self) -> None:
        """Calculate total battery consumption from power samples."""
        if len(self._power_samples) < 2:
            return

        total_energy_mwh = 0.0
        total_voltage_mv = 0.0

        for i in range(1, len(self._power_samples)):
            prev_ts, prev_power, prev_voltage = self._power_samples[i - 1]
            curr_ts, curr_power, curr_voltage = self._power_samples[i]

            dt_hours = (curr_ts - prev_ts) / 3600.0
            avg_power = (prev_power + curr_power) / 2.0
            energy_mwh = avg_power * dt_hours
            total_energy_mwh += energy_mwh
            total_voltage_mv += (prev_voltage + curr_voltage) / 2.0

        if total_energy_mwh > 0 and total_voltage_mv > 0:
            avg_voltage_mv = total_voltage_mv / (len(self._power_samples) - 1)
            self.battery_drain_mah = round((total_energy_mwh / avg_voltage_mv) * 1000.0, 2)
