"""Integration tests for Android device and monitoring with mocked ADB."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from play_monkey.config.models import (
    BoundsConfig,
    EventRatios,
    MonitoringConfig,
    Platform,
    StabilityConfig,
    TestConfig,
)
from play_monkey.core.events import EventGenerator, TapEvent, SwipeEvent
from play_monkey.core.scheduler import MonkeyScheduler
from play_monkey.monitoring.android_monitor import AndroidMonitor
from play_monkey.monitoring.metrics import MetricSample
from play_monkey.reporting.statistics import Statistics
from play_monkey.stability.android_monitor import AndroidStabilityMonitor
from play_monkey.stability.models import StabilityIssueType


# ── Android device tests ────────────────────────────────────────────


class TestAndroidDeviceMocked:
    """Test AndroidDevice with mocked subprocess and minitouch session."""

    def _make_fake_session(self):
        """Create a fake async MinitouchSession with a parsed banner."""
        from play_monkey.devices.minitouch import MinitouchBanner

        async def noop(*args, **kwargs):
            return None

        session = MagicMock()
        session.open = MagicMock(side_effect=noop)
        session.tap = MagicMock(side_effect=noop)
        session.swipe = MagicMock(side_effect=noop)
        session.close = MagicMock(side_effect=noop)
        session.banner = MinitouchBanner(
            max_contacts=10, max_x=1080, max_y=2340, max_pressure=255
        )
        return session

    def _fake_run(self, abi="arm64-v8a", get_state="device\n"):
        """Build a subprocess.run side_effect covering the connect() calls."""
        def run(cmd, *args, **kwargs):
            if "get-state" in cmd:
                return MagicMock(returncode=0, stdout=get_state, stderr="")
            if "wm size" in cmd:
                return MagicMock(
                    returncode=0, stdout="Physical size: 1080x2340\n", stderr=""
                )
            if "ro.product.cpu.abi" in cmd:
                return MagicMock(returncode=0, stdout=f"{abi}\n", stderr="")
            if "push" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        return run

    @patch("play_monkey.devices.android.MinitouchSession")
    @patch("play_monkey.devices.android.subprocess.run")
    def test_connect_success(self, mock_run, mock_session_cls):
        from play_monkey.devices.android import AndroidDevice

        mock_run.side_effect = self._fake_run()
        mock_session_cls.return_value = self._make_fake_session()

        device = AndroidDevice("emulator-5554")
        assert device.connect() is True
        device.disconnect()

    @patch("play_monkey.devices.android.subprocess.run")
    def test_connect_failure(self, mock_run):
        from play_monkey.devices.android import AndroidDevice

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        device = AndroidDevice("emulator-5554")
        assert device.connect() is False

    @patch("play_monkey.devices.android.subprocess.run")
    def test_connect_unsupported_abi_fails(self, mock_run):
        """No bundled binary for the ABI -> connect fails loudly, no fallback."""
        from play_monkey.devices.android import AndroidDevice

        mock_run.side_effect = self._fake_run(abi="mips64")
        device = AndroidDevice("emulator-5554")
        assert device.connect() is False

    @patch("play_monkey.devices.android.MinitouchSession")
    @patch("play_monkey.devices.android.subprocess.run")
    def test_connect_minitouch_open_failure_fails(self, mock_run, mock_session_cls):
        """If minitouch can't start, connect fails (no silent input fallback)."""
        from play_monkey.devices.android import AndroidDevice

        mock_run.side_effect = self._fake_run()

        async def boom(*args, **kwargs):
            raise ConnectionError("socket unavailable")

        session = MagicMock()
        session.open = MagicMock(side_effect=boom)
        mock_session_cls.return_value = session

        device = AndroidDevice("emulator-5554")
        assert device.connect() is False

    @patch("play_monkey.devices.android.subprocess.run")
    def test_get_screen_size(self, mock_run):
        from play_monkey.devices.android import AndroidDevice

        mock_run.return_value = MagicMock(
            returncode=0, stdout="Physical size: 1080x1920\n"
        )
        device = AndroidDevice("emulator-5554")
        w, h = device.get_screen_size()
        assert w == 1080
        assert h == 1920

    @patch("play_monkey.devices.android.MinitouchSession")
    @patch("play_monkey.devices.android.subprocess.run")
    def test_tap(self, mock_run, mock_session_cls):
        from play_monkey.devices.android import AndroidDevice

        mock_run.side_effect = self._fake_run()
        fake_session = self._make_fake_session()
        mock_session_cls.return_value = fake_session

        device = AndroidDevice("emulator-5554")
        device.connect()
        result = device.tap(100, 200)
        assert result is True
        assert fake_session.tap.called
        device.disconnect()

    @patch("play_monkey.devices.android.MinitouchSession")
    @patch("play_monkey.devices.android.subprocess.run")
    def test_swipe(self, mock_run, mock_session_cls):
        from play_monkey.devices.android import AndroidDevice

        mock_run.side_effect = self._fake_run()
        fake_session = self._make_fake_session()
        mock_session_cls.return_value = fake_session

        device = AndroidDevice("emulator-5554")
        device.connect()
        result = device.swipe(100, 200, 300, 400, 500)
        assert result is True
        assert fake_session.swipe.called
        device.disconnect()

    @patch("play_monkey.devices.android.MinitouchSession")
    @patch("play_monkey.devices.android.subprocess.run")
    def test_session_reused_across_commands(self, mock_run, mock_session_cls):
        from play_monkey.devices.android import AndroidDevice

        mock_run.side_effect = self._fake_run()
        mock_session_cls.return_value = self._make_fake_session()

        device = AndroidDevice("emulator-5554")
        device.connect()
        device.tap(100, 200)
        device.tap(300, 400)
        device.swipe(100, 200, 300, 400, 500)
        device.disconnect()

        # One minitouch session created and reused for all gestures.
        assert mock_session_cls.call_count == 1


# ── Android monitor tests (SoloX-based) ────────────────────────────


class TestAndroidMonitorMocked:
    """Test AndroidMonitor with mocked SoloX collectors."""

    @patch("play_monkey.monitoring.android_monitor.CPU")
    @patch("play_monkey.monitoring.android_monitor.Memory")
    @patch("play_monkey.monitoring.android_monitor.FPS")
    @patch("play_monkey.monitoring.android_monitor.Battery")
    @patch("play_monkey.monitoring.android_monitor.subprocess.run")
    def test_start_monitoring_initializes_collectors(
        self, mock_run, mock_battery, mock_fps, mock_memory, mock_cpu
    ):
        """Test that start_monitoring initializes SoloX collectors."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        
        monitor = AndroidMonitor("emulator-5554")
        monitor.start_monitoring("com.example.app")
        
        # Verify collectors were initialized
        mock_cpu.assert_called_once()
        mock_memory.assert_called_once()
        mock_fps.assert_called_once()
        mock_battery.assert_called_once()
        
        # Verify batterystats was reset
        assert mock_run.call_count >= 1
        
        monitor.stop_monitoring()

    @patch("play_monkey.monitoring.android_monitor.subprocess.run")
    def test_battery_stats_parsing(self, mock_run):
        """Test parsing of dumpsys batterystats output."""
        # Mock batterystats output
        batterystats_output = """
        Computed drain: 12.34 mAh
        CPU: 5.67 mAh
        Screen: 3.21 mAh
        Wifi: 1.23 mAh
        """
        mock_run.return_value = MagicMock(returncode=0, stdout=batterystats_output)
        
        monitor = AndroidMonitor("emulator-5554")
        monitor.app_package = "com.example.app"
        monitor._parse_battery_stats()
        
        assert monitor.battery_drain_mah == 12.34
        assert monitor.battery_components.get("Cpu") == 5.67
        assert monitor.battery_components.get("Screen") == 3.21
        assert monitor.battery_components.get("Wifi") == 1.23


# ── Statistics tests ────────────────────────────────────────────────


class TestStatistics:
    """Test statistics computation."""

    def test_compute_all_stats(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        stats = Statistics.compute_all_stats(values)

        assert stats["max"] == 100.0
        assert stats["avg"] == 55.0
        assert stats["p50"] == 55.0
        assert stats["p90"] == 91.0
        assert abs(stats["p95"] - 95.5) < 0.01
        assert stats["p99"] == 99.1

    def test_compute_all_stats_empty(self):
        stats = Statistics.compute_all_stats([])
        assert stats["max"] == 0
        assert stats["avg"] == 0
        assert stats["p50"] == 0

    def test_compute_all_stats_single_value(self):
        stats = Statistics.compute_all_stats([42.0])
        assert stats["max"] == 42.0
        assert stats["avg"] == 42.0
        assert stats["p50"] == 42.0


# ── Scheduler termination tests ────────────────────────────────────


class TestSchedulerTermination:
    """Test scheduler termination conditions."""

    def test_should_continue_by_event_count(self):
        from play_monkey.core.scheduler import MonkeyScheduler

        config = TestConfig(
            platform=Platform.ANDROID,
            device_id="emulator-5554",
            app_package="com.example.app",
            event_ratios=EventRatios(tap=1.0, swipe=0.0),
            interval_ms=100,
            event_count=10,
        )

        scheduler = MonkeyScheduler(config, MagicMock())
        scheduler.events_executed = 5
        assert scheduler.should_continue() is True

        scheduler.events_executed = 10
        assert scheduler.should_continue() is False

    def test_should_continue_by_duration(self):
        from play_monkey.core.scheduler import MonkeyScheduler

        config = TestConfig(
            platform=Platform.ANDROID,
            device_id="emulator-5554",
            app_package="com.example.app",
            event_ratios=EventRatios(tap=1.0, swipe=0.0),
            interval_ms=100,
            duration_seconds=5,
        )

        scheduler = MonkeyScheduler(config, MagicMock())
        scheduler.start_time = datetime.now()
        assert scheduler.should_continue() is True

    def test_crash_limit_stops_test(self):
        from play_monkey.core.scheduler import MonkeyScheduler

        config = TestConfig(
            platform=Platform.ANDROID,
            device_id="emulator-5554",
            app_package="com.example.app",
            event_ratios=EventRatios(tap=1.0, swipe=0.0),
            interval_ms=100,
            event_count=1000,
            stability=StabilityConfig(max_crash_count=3),
        )

        scheduler = MonkeyScheduler(config, MagicMock())
        scheduler.crash_count = 3
        assert scheduler.should_continue() is False


# ── Scheduler sampling decoupling tests ─────────────────────────────


