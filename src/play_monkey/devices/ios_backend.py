"""iOS device backend abstraction.

Supports two backends:
- tidevice: For iOS 16 and below (requires DeveloperDiskImage)
- tidevice3/pymobiledevice3: For iOS 17+ (uses RemoteServiceDiscoveryService)

Both backends support automatic WDA startup.
"""

import subprocess
import time
import threading
import logging
from abc import ABC, abstractmethod
from typing import Optional
import socket as _socket

logger = logging.getLogger(__name__)


class IOSBackend(ABC):
    """Abstract base class for iOS device backends."""

    @abstractmethod
    def connect(self, device_id: str) -> bool:
        """Connect to device and initialize backend."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from device and cleanup resources."""
        pass

    @abstractmethod
    def create_wda_connection(self, port: int = 8100) -> _socket.socket:
        """Create a socket connection to WDA port."""
        pass

    @abstractmethod
    def get_device_info(self) -> dict:
        """Get device information including iOS version."""
        pass

    @abstractmethod
    def start_wda(self, bundle_pattern: str = "com.*.xctrunner") -> bool:
        """Start WebDriverAgent on device."""
        pass


class TideviceBackend(IOSBackend):
    """Backend using tidevice (for iOS 16 and below)."""

    def __init__(self, reuse_existing_wda: bool = False):
        """
        Args:
            reuse_existing_wda: If True and WDA is already listening on
                localhost:8100, use it instead of starting tidevice
                wdaproxy ourselves. Off by default because the auto-probe
                can race with a still-exiting WDA from the previous run
                and pick up a half-dead session. Turn this on when you
                manually started WDA via Xcode / iproxy / tidevice relay
                and specifically want to bypass the wdaproxy wrapper.
        """
        self.device_id: Optional[str] = None
        self._td_device = None
        self._wda_thread: Optional[threading.Thread] = None
        self._wda_process = None  # subprocess for tidevice wdaproxy
        self._reuse_existing_wda = reuse_existing_wda

    def connect(self, device_id: str) -> bool:
        """Connect to device using tidevice."""
        try:
            import tidevice
            self.device_id = device_id
            self._td_device = tidevice.Device(device_id)

            # Verify connection
            _ = self._td_device.info
            logger.info(f"Connected to device via tidevice: {device_id}")
            return True
        except Exception as e:
            logger.error(f"tidevice backend connection failed: {e}")
            return False

    def disconnect(self) -> None:
        """Disconnect from device.

        If we started wdaproxy ourselves, kill its entire process group
        so children (tidevice relay, tidevice xctest) don't become
        orphans. If the user started WDA manually (Xcode / their own
        forwarder) we leave everything alone - their tooling owns those
        processes, not us.
        """
        import os
        import signal

        we_started_wda = self._wda_process is not None

        if we_started_wda:
            pid = self._wda_process.pid
            try:
                # Kill the whole process group we created with start_new_session=True
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                try:
                    self._wda_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                    self._wda_process.wait(timeout=2)
            except (ProcessLookupError, PermissionError):
                # Already gone - fine
                pass
            except Exception as e:
                logger.debug(f"killpg failed, falling back to kill: {e}")
                try:
                    self._wda_process.kill()
                    self._wda_process.wait(timeout=2)
                except Exception:
                    pass
            self._wda_process = None

            # Belt and suspenders: sweep for any stragglers tied to this
            # device. Only do this if we started wdaproxy ourselves, so
            # we don't kill the user's own tidevice relay / xctest.
            self._cleanup_leftover_processes()

        self._td_device = None
        self.device_id = None

    def create_wda_connection(self, port: int = 8100) -> _socket.socket:
        """Create a socket connection to WDA port.

        When using tidevice wdaproxy, WDA is accessible via localhost:8100.
        """
        if self._td_device is None:
            raise ConnectionError("Device not connected")

        # Connect to localhost since tidevice wdaproxy proxies to localhost:8100
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        return sock

    def get_device_info(self) -> dict:
        """Get device information."""
        if self._td_device is None:
            raise ConnectionError("Device not connected")

        info = self._td_device.info
        version = self._td_device.get_value('ProductVersion')

        return {
            'udid': info.udid,
            'ios_version': version,
            'backend': 'tidevice'
        }

    def start_wda(self, bundle_pattern: str = "com.*.xctrunner") -> bool:
        """Ensure WDA is reachable on localhost:8100.

        Default behavior: always start WDA via `tidevice wdaproxy`. We
        run _cleanup_leftover_processes first so any wdaproxy / relay /
        xctest processes from a previous run are torn down before the
        new one comes up.

        Opt-in fast path: if self._reuse_existing_wda is True and WDA
        is already listening, use it as-is. Don't enable this unless
        you manually started WDA (e.g. from Xcode) and already forwarded
        the port - otherwise the probe can race with a still-exiting
        previous-run WDA and pick up a half-dead session.
        """
        if self._td_device is None:
            return False

        if self._reuse_existing_wda and self._wda_reachable(timeout=2.0):
            logger.info(
                "reuse_existing_wda=True: using existing WDA on localhost:8100"
            )
            return True

        try:
            import subprocess
            import os

            # Clean up any leftover tidevice processes that might be occupying port 8100
            # This handles cases where previous runs didn't clean up properly
            self._cleanup_leftover_processes()

            logger.info("Starting WDA via tidevice wdaproxy...")

            # Start WDA proxy process in its own process group so we can
            # reliably kill the whole tree (wdaproxy spawns 'tidevice relay'
            # and 'tidevice xctest' children) on disconnect.
            self._wda_process = subprocess.Popen(
                [
                    "tidevice",
                    "-u", self.device_id,
                    "wdaproxy",
                    "-B", bundle_pattern,
                    "--port", "8100"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,  # POSIX: setsid() so Popen.pid is the pgid
            )

            logger.info(f"WDA proxy started with PID: {self._wda_process.pid}")

            # Wait for WDA to be ready
            logger.info("Waiting for WDA to be ready...")
            deadline = time.time() + 60.0
            while time.time() < deadline:
                # Check if process is still running
                if self._wda_process.poll() is not None:
                    stdout, stderr = self._wda_process.communicate()
                    logger.error(f"WDA process exited with code {self._wda_process.returncode}")
                    logger.error(f"STDOUT: {stdout}")
                    logger.error(f"STDERR: {stderr}")
                    return False

                if self._wda_reachable(timeout=2.0):
                    logger.info("WDA is ready and accessible")
                    return True
                time.sleep(1.0)

            logger.error("WDA did not start in time")
            return False

        except Exception as e:
            logger.error(f"Failed to start WDA: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    @staticmethod
    def _wda_reachable(timeout: float = 2.0) -> bool:
        """Probe localhost:8100 for a live WDA instance."""
        try:
            import urllib.request
            with urllib.request.urlopen(
                "http://127.0.0.1:8100/status", timeout=timeout
            ) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def _cleanup_leftover_processes(self) -> None:
        """Kill any leftover tidevice processes that may be occupying port 8100.

        Previous runs that didn't clean up properly can leave processes like
        'tidevice wdaproxy' or 'tidevice relay' running, blocking port 8100
        and preventing new WDA sessions from starting.
        """
        try:
            import subprocess
            # Find and kill tidevice processes for this device
            # Use pgrep to find processes matching the pattern
            patterns = [
                f"tidevice.*-u.*{self.device_id}.*wdaproxy",
                f"tidevice.*-u.*{self.device_id}.*relay.*8100",
                f"tidevice.*-u.*{self.device_id}.*xctest",
            ]
            for pattern in patterns:
                try:
                    result = subprocess.run(
                        ["pgrep", "-f", pattern],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        pids = result.stdout.strip().split("\n")
                        for pid in pids:
                            try:
                                subprocess.run(["kill", pid], timeout=2)
                                logger.info(f"Killed leftover tidevice process: PID {pid}")
                            except Exception:
                                pass
                except Exception:
                    continue

            # Give processes a moment to release the port
            time.sleep(1.0)
        except Exception as e:
            logger.debug(f"Cleanup warning (non-critical): {e}")


class Tidevice3Backend(IOSBackend):
    """Backend using tidevice3/pymobiledevice3 (for iOS 17+)."""

    def __init__(self):
        self.device_id: Optional[str] = None
        self._service_provider = None
        self._tunneld_process = None
        self._wda_threads = []
        self._forwarder = None

    def connect(self, device_id: str) -> bool:
        """Connect to device using tidevice3."""
        try:
            self.device_id = device_id

            # Ensure tunneld is running
            if not self._ensure_tunneld():
                logger.error("Failed to start tunneld service")
                return False

            # Wait for tunneld to establish tunnel for this device
            tunnel_address = self._wait_for_device_tunnel(device_id)
            if tunnel_address is None:
                logger.error(f"tunneld did not establish tunnel for device {device_id}")
                return False

            # Connect to device using the tunnel address directly
            logger.info(f"Connecting to device via tidevice3: {device_id}")
            self._service_provider = self._connect_via_tunnel(device_id, tunnel_address)

            if self._service_provider is None:
                logger.error("Failed to create service provider")
                return False

            logger.info(f"Connected successfully, iOS version: {self._service_provider.product_version}")
            return True

        except Exception as e:
            logger.error(f"tidevice3 backend connection failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _connect_via_tunnel(self, device_id: str, tunnel_address):
        """Connect to device using RemoteServiceDiscoveryService directly.

        Bypasses tidevice3's connect_service_provider to handle different
        return formats from tunneld.
        """
        from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
        from pymobiledevice3.utils import get_asyncio_loop

        # Normalize tunnel_address to (host, port) tuple
        if isinstance(tunnel_address, (list, tuple)) and len(tunnel_address) >= 2:
            host = str(tunnel_address[0])
            port = int(tunnel_address[1])
            address = (host, port)
        else:
            logger.error(f"Unexpected tunnel address format: {tunnel_address}")
            return None

        logger.info(f"Creating RSD connection to {address}")

        # Create and connect RSD service
        rsd = RemoteServiceDiscoveryService(address)
        try:
            get_asyncio_loop().run_until_complete(rsd.connect())
            return rsd
        except Exception as e:
            logger.error(f"Failed to connect RSD: {e}")
            return None

    def _wait_for_device_tunnel(self, device_id: str, timeout: float = 30.0):
        """Wait for tunneld to establish tunnel for the device.

        Returns:
            Tunnel address (tuple/list of [host, port]) if ready, None otherwise.
        """
        import requests

        # Determine tunneld URL
        tunneld_url = None
        for port in [49151, 5555]:
            try:
                resp = requests.get(f"http://localhost:{port}", timeout=1)
                if resp.status_code == 200:
                    tunneld_url = f"http://localhost:{port}"
                    break
            except Exception:
                continue

        if tunneld_url is None:
            logger.error("tunneld not reachable")
            return None

        logger.info(f"Waiting for tunneld to establish tunnel for device {device_id}...")

        deadline = time.time() + timeout
        last_log_time = 0
        while time.time() < deadline:
            try:
                resp = requests.get(tunneld_url, timeout=2)
                tunnels = resp.json()
                if device_id in tunnels:
                    tunnel_address = tunnels.get(device_id)
                    if tunnel_address:
                        logger.info(f"Tunnel ready for device: {tunnel_address}")
                        return tunnel_address

                # Log progress every 5 seconds
                if time.time() - last_log_time > 5:
                    available_devices = list(tunnels.keys())
                    logger.info(f"Tunnel not ready yet. Available devices in tunneld: {available_devices}")
                    last_log_time = time.time()

            except Exception as e:
                logger.debug(f"Error checking tunneld: {e}")

            time.sleep(1)

        logger.error(f"Timeout waiting for tunnel (waited {timeout}s)")
        return None

    def disconnect(self) -> None:
        """Disconnect from device."""
        # Stop forwarder
        if self._forwarder:
            try:
                self._forwarder.stop()
            except Exception:
                pass
            self._forwarder = None

        # Close service provider
        if self._service_provider:
            try:
                if hasattr(self._service_provider, '__exit__'):
                    self._service_provider.__exit__(None, None, None)
            except Exception:
                pass
            self._service_provider = None

        self.device_id = None

    def create_wda_connection(self, port: int = 8100) -> _socket.socket:
        """Create a socket connection to WDA port.

        For tidevice3, we connect to the local forwarded port.
        """
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.connect(('localhost', port))
        return sock

    def get_device_info(self) -> dict:
        """Get device information."""
        if self._service_provider is None:
            raise ConnectionError("Device not connected")

        return {
            'udid': self._service_provider.udid,
            'ios_version': self._service_provider.product_version,
            'backend': 'tidevice3'
        }

    def start_wda(self, bundle_pattern: str = "com.*.xctrunner") -> bool:
        """Start WDA or connect to already-running WDA.

        For iOS 17+: Automatic WDA launch via XCUITestService has known issues.
        The recommended approach is to start WDA manually via Xcode.

        For iOS 16-: Automatic launch via xctest() works reliably.
        """
        if self._service_provider is None:
            return False

        # First, check if WDA is already running
        if self._check_wda_running():
            logger.info("WDA is already running, reusing it")
            return self._setup_port_forwarder()

        # WDA is not running - provide clear instructions
        logger.warning("WDA is not running on device")
        logger.warning("")
        logger.warning("For iOS 17+, automatic WDA launch has known issues.")
        logger.warning("Please start WDA manually via Xcode:")
        logger.warning("  1. Open WebDriverAgent.xcodeproj in Xcode")
        logger.warning("  2. Select WebDriverAgentRunner target")
        logger.warning("  3. Select your device")
        logger.warning("  4. Product → Test (Cmd+U)")
        logger.warning("  5. Wait for 'ServerURLHere->' in Xcode console")
        logger.warning("  6. Keep Xcode test running")
        logger.warning("  7. Re-run this test")
        logger.warning("")

        # For iOS 17+, don't attempt automatic launch as it's unreliable
        # Just fail with clear instructions
        return False

    def _check_wda_running(self) -> bool:
        """Check if WDA is already running on device."""
        try:
            # Try to connect via usbmux to device port 8100
            import tidevice
            d = tidevice.Device(self.device_id)
            conn = d.create_inner_connection(8100)
            sock = conn.psock._sock
            sock.settimeout(3.0)
            sock.sendall(b'GET /status HTTP/1.1\r\nHost: localhost\r\n\r\n')
            response = sock.recv(1024)
            sock.close()
            return b'200' in response and b'WebDriverAgent' in response
        except Exception:
            return False

    def _setup_port_forwarder(self) -> bool:
        """Set up local port forwarder for WDA.

        Creates a local port (8100) that forwards to device's WDA.
        """
        try:
            from pymobiledevice3.tcp_forwarder import UsbmuxTcpForwarder

            logger.info("Setting up TCP forwarder: localhost:8100 -> device:8100")

            listening_event = threading.Event()
            self._forwarder = UsbmuxTcpForwarder(
                self._service_provider.udid,
                8100,  # device port
                8100,  # local port
                listening_event=listening_event
            )

            def forwarder_start():
                try:
                    self._forwarder.start('127.0.0.1')
                except Exception as e:
                    logger.error(f"Forwarder error: {e}")

            thread_fwd = threading.Thread(target=forwarder_start, daemon=True, name="wda-forwarder")
            thread_fwd.start()
            self._wda_threads.append(thread_fwd)

            if not listening_event.wait(timeout=10):
                logger.error("TCP forwarder did not start listening")
                return False

            # Give forwarder a moment to settle
            time.sleep(0.5)
            logger.info("TCP forwarder is ready on 127.0.0.1:8100")
            return True

        except Exception as e:
            logger.error(f"Failed to setup port forwarder: {e}")
            return False

    def _ensure_tunneld(self) -> bool:
        """Ensure tunneld service is running."""
        # Check if tunneld is already running on either port
        for port in [49151, 5555]:
            try:
                import requests
                resp = requests.get(f"http://localhost:{port}", timeout=1)
                if resp.status_code == 200:
                    logger.info(f"tunneld is already running on port {port}")
                    return True
            except Exception:
                pass

        # Try to start tunneld via osascript (will prompt for password)
        logger.info("tunneld is not running, attempting to start with sudo...")
        logger.info("You may be prompted for your password")

        try:
            import shutil
            t3_path = shutil.which('t3')
            if not t3_path:
                logger.error("t3 command not found in PATH")
                return False

            # Use osascript to request admin privileges
            script = f'''
            do shell script "{t3_path} tunneld > /tmp/tunneld.log 2>&1 &" with administrator privileges
            '''

            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.error(f"Failed to start tunneld: {result.stderr}")
                self._print_manual_instructions()
                return False

            # Wait for tunneld to be ready
            logger.info("Waiting for tunneld to start...")
            for i in range(20):
                time.sleep(1)
                try:
                    import requests
                    resp = requests.get("http://localhost:5555", timeout=1)
                    if resp.status_code == 200:
                        logger.info(f"tunneld started successfully (took {i+1}s)")
                        return True
                except Exception:
                    continue

            logger.error("tunneld did not become ready in time")
            self._print_manual_instructions()
            return False

        except subprocess.TimeoutExpired:
            logger.error("Timeout waiting for password input")
            self._print_manual_instructions()
            return False
        except Exception as e:
            logger.error(f"Failed to start tunneld: {e}")
            self._print_manual_instructions()
            return False

    def _print_manual_instructions(self) -> None:
        """Print manual instructions for starting tunneld."""
        logger.error("")
        logger.error("Please start tunneld manually in another terminal:")
        logger.error("  sudo t3 tunneld")
        logger.error("")
        logger.error("Or run in background:")
        logger.error("  sudo t3 tunneld > /tmp/tunneld.log 2>&1 &")
        logger.error("")


def detect_ios_version(device_id: str) -> Optional[str]:
    """Detect iOS version of a device.

    Returns:
        iOS version string (e.g., "17.0", "16.5") or None if detection fails
    """
    # Try tidevice first (works for all versions)
    try:
        import tidevice
        d = tidevice.Device(device_id)
        version = d.get_value('ProductVersion')
        logger.info(f"Detected iOS version via tidevice: {version}")
        return version
    except Exception:
        pass

    # Try tidevice3
    try:
        from tidevice3.api import list_devices
        devices = list_devices()
        for dev in devices:
            if dev.Identifier == device_id:
                logger.info(f"Detected iOS version via tidevice3: {dev.ProductVersion}")
                return dev.ProductVersion
    except Exception:
        pass

    logger.warning("Failed to detect iOS version")
    return None


def create_backend(device_id: str, force_backend: Optional[str] = None) -> Optional[IOSBackend]:
    """Create appropriate backend based on iOS version.

    Args:
        device_id: Device UDID
        force_backend: Force specific backend ('tidevice' or 'tidevice3')

    Returns:
        IOSBackend instance or None if creation fails
    """
    if force_backend:
        if force_backend == 'tidevice':
            logger.info("Using tidevice backend (forced)")
            return TideviceBackend()
        elif force_backend == 'tidevice3':
            logger.info("Using tidevice3 backend (forced)")
            return Tidevice3Backend()
        else:
            raise ValueError(f"Unknown backend: {force_backend}")

    # Auto-detect based on iOS version
    ios_version = detect_ios_version(device_id)
    if ios_version is None:
        logger.warning("Failed to detect iOS version, defaulting to tidevice")
        return TideviceBackend()

    major_version = int(ios_version.split('.')[0])

    if major_version >= 17:
        logger.info(f"iOS {ios_version} detected, using tidevice3 backend")
        return Tidevice3Backend()
    else:
        logger.info(f"iOS {ios_version} detected, using tidevice backend")
        return TideviceBackend()
