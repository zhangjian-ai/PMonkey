"""iOS device implementation with automatic backend selection.

Supports two backends:
- tidevice: For iOS 16 and below
- tidevice3/pymobiledevice3: For iOS 17+

The backend is automatically selected based on iOS version.

Touch operations are synchronous: tap/swipe send the HTTP request to
WDA and wait for the response. WDA's XCUITest server is strictly serial
(events execute on iOS's UI main thread), so queueing requests from the
client side only creates a backlog inside WDA that eventually drops the
XCUITest session. Pacing the scheduler to WDA's actual response time
is the only stable way to run.
"""

import http.client
import json
import threading
import time
import logging
from typing import Optional, Tuple

from .base import Device
from .ios_backend import create_backend, IOSBackend

logger = logging.getLogger(__name__)

WDA_DEVICE_PORT = 8100


class _WdaHttpClient:
    """HTTP/1.1 client for WDA, backed by http.client.HTTPConnection.

    Earlier versions of this class drove the socket by hand (building
    the request line, parsing headers, tracking Content-Length). That
    mostly worked but had subtle bugs around keep-alive:

    - If the server closed the connection mid-response, recv() returned
      empty and we reported failure, but the (now-dead) socket stayed
      cached. Next request hit a dead socket.
    - If a response's body length didn't match Content-Length exactly,
      leftover bytes stayed in the socket buffer. The next request
      parsed those bytes as the new status line -> garbage -> cascading
      failures.
    - WDA's response to POST /session can ship as two back-to-back
      frames. The peek-and-read-twice logic half-handled this but any
      hiccup in the second read left partial data in the socket.

    Those failures came in bursts and looked exactly like "WDA session
    died" even though WDA was fine. To get rid of that whole class of
    bugs we let http.client handle the wire format: it consumes the
    full body, knows about Connection: close, and reconnects on demand.
    """

    def __init__(self, host: str, port: int, default_timeout: float = 5.0):
        self._host = host
        self._port = port
        self._default_timeout = default_timeout
        self._conn: Optional[http.client.HTTPConnection] = None
        self._lock = threading.Lock()

    def _ensure_conn(self, timeout: float) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = http.client.HTTPConnection(self._host, self._port, timeout=timeout)
        else:
            # Update per-request timeout without reconnecting.
            self._conn.timeout = timeout
        return self._conn

    def _drop_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def request(
        self, method: str, path: str, body: Optional[dict] = None,
        timeout: float = 5.0,
    ) -> Tuple[bool, dict]:
        """Send an HTTP request and return (ok, parsed_json_body).

        A single attempt, no auto-retry. Retrying POST /actions behind
        the caller's back is unsafe: if the first attempt actually fired
        a touch on the device but the response read failed, a retry
        would fire a second user-visible touch. The caller is expected
        to handle failure by deciding whether to retry at the next
        scheduler tick.
        """
        payload = None
        headers = {}
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"

        with self._lock:
            conn = self._ensure_conn(timeout)
            try:
                conn.request(method, path, body=payload, headers=headers)
                resp = conn.getresponse()
                raw = resp.read()  # always drain the body so keep-alive works
                ok = 200 <= resp.status < 300
            except Exception as e:
                logger.debug(f"WDA HTTP error on {method} {path}: {e}")
                self._drop_conn()
                return False, {}

            data: dict = {}
            if raw:
                try:
                    data = json.loads(raw.decode("utf-8", errors="replace"))
                except Exception:
                    data = {}
            return ok, data

    def close(self) -> None:
        with self._lock:
            self._drop_conn()



class IOSDevice(Device):
    """iOS device with automatic backend selection based on iOS version."""

    def __init__(self, device_id: str, force_backend: Optional[str] = None):
        """Initialize iOS device.

        Args:
            device_id: Device UDID
            force_backend: Force specific backend ('tidevice' or 'tidevice3')
                          If None, backend is auto-selected based on iOS version
        """
        self.device_id = device_id
        self.force_backend = force_backend
        self._connected = False
        self._screen_width: Optional[int] = None
        self._screen_height: Optional[int] = None
        self._session_id: Optional[str] = None
        self._backend: Optional[IOSBackend] = None
        self._http: Optional[_WdaHttpClient] = None

    # ── Device interface ────────────────────────────────────────────

    def connect(self) -> bool:
        """Connect to device and launch WDA."""
        # Create appropriate backend
        logger.info(f"Connecting to iOS device: {self.device_id}")
        self._backend = create_backend(self.device_id, self.force_backend)

        if self._backend is None:
            logger.error("Failed to create backend")
            return False

        # Connect to device
        if not self._backend.connect(self.device_id):
            logger.error("Backend connection failed")
            return False

        # Get device info
        try:
            info = self._backend.get_device_info()
            logger.info(f"Device info: {info}")
        except Exception as e:
            logger.error(f"Failed to get device info: {e}")
            return False

        # Start WDA
        logger.info("Starting WebDriverAgent...")
        if not self._backend.start_wda():
            logger.error("Failed to start WDA")
            logger.error("Please ensure:")
            logger.error("  1. WebDriverAgent is installed on the device")
            logger.error("  2. WDA is properly signed with your development certificate")
            logger.error("  3. Device trusts your development certificate")
            return False

        # Build HTTP client. Both backends proxy WDA to localhost:8100,
        # so we can just talk plain HTTP to that port and let http.client
        # manage keep-alive / framing for us.
        self._http = _WdaHttpClient("127.0.0.1", WDA_DEVICE_PORT)

        # Create WDA session
        logger.info("Creating WDA session...")
        ok, resp = self._http.request("POST", "/session", {"capabilities": {}}, timeout=10.0)

        if ok:
            self._session_id = resp.get("sessionId") or resp.get("value", {}).get("sessionId")

        if not self._session_id:
            logger.error("Failed to create WDA session")
            return False

        self._connected = True
        self._screen_width, self._screen_height = self.get_screen_size()

        # Warm up WDA before letting the scheduler pump events. iOS 15-
        # needs several seconds after session creation before XCUITest
        # synthesis is fully spun up; the first few taps land jerky
        # otherwise. 5s is empirically enough on real hardware and costs
        # nothing compared to the total test runtime.
        warmup_seconds = 5.0
        logger.info(f"Warming up WDA for {warmup_seconds:.0f}s...")
        time.sleep(warmup_seconds)

        logger.info(f"WDA ready (screen: {self._screen_width}x{self._screen_height})")
        return True

    def disconnect(self) -> None:
        """Disconnect from device."""
        if self._http and self._session_id:
            try:
                self._http.request("DELETE", f"/session/{self._session_id}", timeout=3.0)
            except Exception:
                pass
            self._session_id = None

        if self._http:
            self._http.close()
            self._http = None

        if self._backend:
            self._backend.disconnect()
            self._backend = None

        self._connected = False

    def get_screen_size(self) -> Tuple[int, int]:
        """Get device screen size."""
        if self._screen_width and self._screen_height:
            return (self._screen_width, self._screen_height)

        if self._http and self._session_id:
            ok, resp = self._http.request(
                "GET", f"/session/{self._session_id}/window/size", timeout=3.0
            )
            if ok:
                val = resp.get("value", {})
                w, h = val.get("width"), val.get("height")
                if w and h:
                    self._screen_width = int(w)
                    self._screen_height = int(h)
                    return (self._screen_width, self._screen_height)

        return (393, 852)

    def tap(self, x: int, y: int) -> bool:
        """Tap at coordinates using W3C Actions API, synchronously.

        Returns whatever WDA says. A False is just a failed tap, not a
        signal that the device is permanently unusable. The scheduler
        will call us again at the next interval regardless.
        """
        if not self._http or not self._session_id:
            return False

        actions_body = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }

        # 5s timeout. Healthy taps are 100-300ms, but on a busy app or
        # during a transition the same tap can legitimately take 3-4s
        # and still succeed. 5s covers those cases while capping how
        # long we wait on a genuinely stuck request.
        #
        # Note: we cannot make WDA faster. If the observed per-event
        # cost is e.g. 4s, that's the device's true throughput for the
        # current app state, and the scheduler's ticker naturally paces
        # to it (interval_ms floors the cadence, WDA determines the
        # ceiling). Shorter timeouts don't help - they'd just turn
        # healthy-but-slow responses into failures.
        ok, _ = self._http.request(
            "POST", f"/session/{self._session_id}/actions",
            actions_body, timeout=5.0,
        )
        return ok

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> bool:
        """Swipe from (x1, y1) to (x2, y2) using W3C Actions API.

        Uses /session/{id}/actions instead of /wda/dragfromtoforduration
        because the dragfromtoforduration endpoint has a fixed ~800ms
        setup overhead and effectively ignores short durations. W3C
        pointerMove duration is respected precisely by WDA.
        """
        if not self._http or not self._session_id:
            return False

        actions_body = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": x1, "y": y1},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerMove", "duration": duration_ms, "x": x2, "y": y2},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }

        # duration + 3s headroom. Same reasoning as tap's 5s: a healthy
        # swipe runs for exactly `duration_ms`, plus a small HTTP
        # round-trip, plus a small WDA overhead for event synthesis.
        # 3s is generous cover for all three without waiting forever on
        # a stuck swipe.
        http_timeout = duration_ms / 1000.0 + 3.0
        ok, _ = self._http.request(
            "POST", f"/session/{self._session_id}/actions",
            actions_body, timeout=http_timeout,
        )
        return ok

    def screenshot(self, save_path: str) -> bool:
        """Take a screenshot."""
        if not self._http or not self._session_id:
            return False

        ok, resp = self._http.request(
            "GET", f"/session/{self._session_id}/screenshot", timeout=10.0
        )
        if not ok:
            return False

        png_b64 = resp.get("value")
        if not png_b64:
            return False

        try:
            import base64
            png_data = base64.b64decode(png_b64)
            with open(save_path, "wb") as f:
                f.write(png_data)
            return True
        except Exception:
            return False

    def is_app_running(self, app_identifier: str) -> bool:
        """Check if app is running using WDA API."""
        if not self._http or not self._session_id:
            return False

        try:
            # Use WDA's /wda/apps/state endpoint to check app state
            # This works for both tidevice and tidevice3 backends
            ok, resp = self._http.request(
                "POST", f"/session/{self._session_id}/wda/apps/state",
                {"bundleId": app_identifier}, timeout=3.0
            )

            if ok:
                # Response format: {"value": 4} where:
                # 1 = not running, 2 = running in background, 3 = running in background (suspended), 4 = running in foreground
                state = resp.get("value", 1)
                return state >= 2  # Running in background or foreground

            return False
        except Exception:
            # If we can't check, assume app is not running to avoid infinite loops
            return False

    def start_app(self, app_identifier: str) -> bool:
        """Start app."""
        if not self._http or not self._session_id:
            return False

        # Use WDA to launch app
        ok, _ = self._http.request(
            "POST", f"/session/{self._session_id}/wda/apps/launch",
            {"bundleId": app_identifier}, timeout=10.0
        )
        return ok

    def stop_app(self, app_identifier: str) -> bool:
        """Stop app."""
        if not self._http or not self._session_id:
            return False

        # Use WDA to terminate app
        ok, _ = self._http.request(
            "POST", f"/session/{self._session_id}/wda/apps/terminate",
            {"bundleId": app_identifier}, timeout=5.0
        )
        return ok
