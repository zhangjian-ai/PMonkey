"""Integration tests for iOS device and monitoring with mocked calls."""

from unittest.mock import MagicMock, patch

import pytest

from play_monkey.stability.ios_monitor import IOSStabilityMonitor
from play_monkey.stability.models import StabilityIssueType


# ── iOS device tests ────────────────────────────────────────────────


class TestIOSDeviceMocked:
    """Test IOSDevice with its HTTP client mocked out.

    tap/swipe are synchronous: they call self._http.request directly.
    Tests mock the HTTP client and assert on the request args.
    """

    def _make_device_with_fakes(self, session_id="sess-1"):
        """Create an IOSDevice with its _http stubbed out."""
        from play_monkey.devices.ios import IOSDevice

        device = IOSDevice("AAAA-BBBB-CCCC-DDDD")

        fake_http = MagicMock()
        fake_http.request = MagicMock(return_value=(True, {}))
        fake_http.close = MagicMock()
        device._http = fake_http
        device._session_id = session_id
        device._connected = True
        return device, fake_http

    @patch("play_monkey.devices.ios.create_backend")
    def test_connect_success(self, mock_create_backend):
        from play_monkey.devices.ios import IOSDevice

        mock_backend = MagicMock()
        mock_backend.connect.return_value = True
        mock_backend.start_wda.return_value = True
        mock_backend.get_device_info.return_value = {
            'udid': 'AAAA-BBBB-CCCC-DDDD',
            'ios_version': '16.0',
            'backend': 'tidevice'
        }
        mock_create_backend.return_value = mock_backend

        # Patch HTTP client: respond OK on POST /session and window/size
        with patch("play_monkey.devices.ios._WdaHttpClient") as HttpCls:
            http_instance = MagicMock()
            http_instance.request = MagicMock(
                side_effect=[
                    (True, {"sessionId": "session-xyz"}),
                    (True, {"value": {"width": 390, "height": 844}}),
                ]
            )
            http_instance.close = MagicMock()
            HttpCls.return_value = http_instance

            # Short-circuit the warmup sleep
            with patch("play_monkey.devices.ios.time.sleep"):
                device = IOSDevice("AAAA-BBBB-CCCC-DDDD")
                assert device.connect() is True
            assert device._session_id == "session-xyz"
            assert device._screen_width == 390
            assert device._screen_height == 844

    def test_tap_sends_w3c_actions_synchronously(self):
        device, fake_http = self._make_device_with_fakes(session_id="s1")
        assert device.tap(100, 200) is True

        args, kwargs = fake_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "/session/s1/actions"

        actions_body = args[2]
        pointer_action = actions_body["actions"][0]
        assert pointer_action["type"] == "pointer"
        assert pointer_action["parameters"]["pointerType"] == "touch"

        # move -> down -> up, no pause
        actions = pointer_action["actions"]
        assert len(actions) == 3
        assert actions[0] == {"type": "pointerMove", "duration": 0, "x": 100, "y": 200}
        assert actions[1]["type"] == "pointerDown"
        assert actions[2]["type"] == "pointerUp"

    def test_swipe_sends_w3c_actions_with_duration(self):
        device, fake_http = self._make_device_with_fakes(session_id="s1")
        assert device.swipe(100, 200, 300, 400, 250) is True

        args, kwargs = fake_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "/session/s1/actions"

        pointer_action = args[2]["actions"][0]
        actions = pointer_action["actions"]
        assert len(actions) == 4
        assert actions[0] == {"type": "pointerMove", "duration": 0, "x": 100, "y": 200}
        assert actions[1]["type"] == "pointerDown"
        assert actions[2] == {"type": "pointerMove", "duration": 250, "x": 300, "y": 400}
        assert actions[3]["type"] == "pointerUp"

    def test_tap_without_session_returns_false(self):
        from play_monkey.devices.ios import IOSDevice
        device = IOSDevice("AAAA-BBBB-CCCC-DDDD")
        assert device.tap(100, 200) is False

    def test_swipe_without_session_returns_false(self):
        from play_monkey.devices.ios import IOSDevice
        device = IOSDevice("AAAA-BBBB-CCCC-DDDD")
        assert device.swipe(100, 200, 300, 400, 250) is False

    def test_http_failure_surfaces_as_false(self):
        device, fake_http = self._make_device_with_fakes(session_id="s1")
        fake_http.request = MagicMock(return_value=(False, {}))
        assert device.tap(10, 20) is False
        assert device.swipe(10, 20, 30, 40, 100) is False

    def test_failure_does_not_permanently_disable_device(self):
        """A failing tap isn't fatal - the next tap still goes out.

        We don't track a failure counter or trigger a session rebuild.
        Each tap is independent: WDA answers, we return the answer,
        move on. A transient WDA stall self-heals naturally because
        the next scheduler tick just sends another request.
        """
        device, fake_http = self._make_device_with_fakes(session_id="s1")
        fake_http.request = MagicMock(
            side_effect=[(False, {}), (False, {}), (False, {}), (True, {})]
        )

        # Three in a row fail, then a success - all four went through.
        assert device.tap(1, 1) is False
        assert device.tap(1, 1) is False
        assert device.tap(1, 1) is False
        assert device.tap(1, 1) is True
        assert fake_http.request.call_count == 4


# ── iOS stability monitor tests ────────────────────────────────────


class TestIOSStabilityMonitorCrashParsing:
    """Test crash report parsing logic."""

    def test_handle_crash_report_extracts_fields(self):
        monitor = IOSStabilityMonitor("AAAA-BBBB-CCCC-DDDD")
        monitor.app_package = "com.example.app"
        monitor._monitoring = True

        crash_lines = [
            "Incident Identifier: 12345-ABCDE",
            "Process:             com.example.app [1234]",
            "Exception Type:      EXC_CRASH (SIGABRT)",
            "Exception Codes:     0x0000000000000000, 0x0000000000000000",
            "Triggered by Thread: 0",
            "",
        ]

        monitor._handle_crash_report(crash_lines)

        assert len(monitor.issues) == 1
        issue = monitor.issues[0]
        assert issue.type == StabilityIssueType.CRASH
        assert issue.severity == "critical"
        assert "EXC_CRASH (SIGABRT)" in issue.message
        assert "com.example.app" in issue.message
        assert issue.stacktrace is not None
        assert "Incident Identifier" in issue.stacktrace

    def test_handle_crash_report_skips_unrelated_app(self):
        monitor = IOSStabilityMonitor("AAAA-BBBB-CCCC-DDDD")
        monitor.app_package = "com.example.app"
        monitor._monitoring = True

        crash_lines = [
            "Incident Identifier: 12345-ABCDE",
            "Process:             com.other.app [5678]",
            "Exception Type:      EXC_CRASH (SIGABRT)",
            "",
        ]

        monitor._handle_crash_report(crash_lines)
        assert len(monitor.issues) == 0

    def test_oslog_error_from_app_subsystem_is_recorded(self):
        monitor = IOSStabilityMonitor("AAAA-BBBB-CCCC-DDDD")
        monitor.app_package = "com.example.app"
        monitor._monitoring = True

        # Real oslog format:
        # [timestamp][subsystem][category][pid][process] <Level>: message
        monitor._process_oslog_line(
            "[2024-01-01 12:00:00.000000][com.example.app][net][1234][Example] <Error>: NSURLSession failed"
        )

        assert len(monitor.issues) == 1
        issue = monitor.issues[0]
        assert issue.type == StabilityIssueType.ERROR
        assert "NSURLSession failed" in issue.message
        assert issue.severity == "medium"

    def test_oslog_fault_is_higher_severity(self):
        monitor = IOSStabilityMonitor("AAAA-BBBB-CCCC-DDDD")
        monitor.app_package = "com.example.app"
        monitor._monitoring = True

        monitor._process_oslog_line(
            "[2024-01-01 12:00:00.000000][com.example.app][][1234][Example] <Fault>: memory warning"
        )
        assert len(monitor.issues) == 1
        assert monitor.issues[0].severity == "high"

    def test_oslog_ignores_unrelated_app(self):
        monitor = IOSStabilityMonitor("AAAA-BBBB-CCCC-DDDD")
        monitor.app_package = "com.example.app"
        monitor._monitoring = True

        monitor._process_oslog_line(
            "[2024-01-01 12:00:00.000000][com.other.app][net][5678][Other] <Error>: failed"
        )
        assert len(monitor.issues) == 0

    def test_oslog_ignores_system_wifi_noise_mentioning_app(self):
        """WiFi policy logs include fgApp: <our bundle> but are NOT our errors.

        This was the real-world bug: WiFi/system logs were being recorded as
        'app errors' because the foreground app identifier showed up in the
        message body and the message contained the substring 'fail' (e.g.
        ``TxFwFail: 0``).
        """
        monitor = IOSStabilityMonitor("AAAA-BBBB-CCCC-DDDD")
        monitor.app_package = "com.example.app"
        monitor._monitoring = True

        monitor._process_oslog_line(
            "[2026-05-08 16:22:54.039274][com.apple.WiFiPolicy][][53][WiFiPolicy] <Default>: "
            "__WiFiLQAMgrLogStats(TTWJ-CD:Stationary): TxFwFail: 0 "
            "fgApp: com.example.app"
        )
        assert len(monitor.issues) == 0

    def test_oslog_ignores_default_level_even_when_from_app(self):
        """Default/Info/Debug levels from the app are not errors."""
        monitor = IOSStabilityMonitor("AAAA-BBBB-CCCC-DDDD")
        monitor.app_package = "com.example.app"
        monitor._monitoring = True

        monitor._process_oslog_line(
            "[2024-01-01 12:00:00.000000][com.example.app][][1234][Example] <Default>: regular log"
        )
        monitor._process_oslog_line(
            "[2024-01-01 12:00:01.000000][com.example.app][][1234][Example] <Info>: info message"
        )
        assert len(monitor.issues) == 0

    def test_deduplication(self):
        monitor = IOSStabilityMonitor("AAAA-BBBB-CCCC-DDDD")
        monitor.app_package = "com.example.app"
        monitor._monitoring = True

        msg = "[2024-01-01 12:00:00.000000][com.example.app][][1234][Example] <Error>: same error"
        monitor._process_oslog_line(msg)
        monitor._process_oslog_line(msg)

        assert len(monitor.issues) == 1
