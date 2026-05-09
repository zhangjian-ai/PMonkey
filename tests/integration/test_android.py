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
    """Test AndroidDevice with mocked subprocess and shell session."""

    def _make_fake_session(self):
        """Create a fake async PersistentShellSession."""
        async def send_ok(cmd):
            return True

        async def noop_close():
            return None

        session = MagicMock()
        session.send_command = MagicMock(side_effect=send_ok)
        session.close = MagicMock(side_effect=noop_close)
        return session

    @patch("play_monkey.devices.android.AdbClient")
    @patch("play_monkey.devices.android.subprocess.run")
    def test_connect_success(self, mock_run, mock_adb_client_cls):
        from play_monkey.devices.android import AndroidDevice

        mock_run.return_value = MagicMock(returncode=0, stdout="device\n")

        fake_session = self._make_fake_session()

        async def fake_open_shell(serial):
            return fake_session

        mock_adb_client_cls.return_value.open_persistent_shell = MagicMock(
            side_effect=fake_open_shell
        )

        device = AndroidDevice("emulator-5554")
        assert device.connect() is True
        device.disconnect()

    @patch("play_monkey.devices.android.subprocess.run")
    def test_connect_failure(self, mock_run):
        from play_monkey.devices.android import AndroidDevice

        mock_run.return_value = MagicMock(returncode=1, stdout="")
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

    @patch("play_monkey.devices.android.AdbClient")
    @patch("play_monkey.devices.android.subprocess.run")
    def test_tap(self, mock_run, mock_adb_client_cls):
        from play_monkey.devices.android import AndroidDevice

        mock_run.return_value = MagicMock(returncode=0, stdout="device\n")

        fake_session = self._make_fake_session()

        async def fake_open_shell(serial):
            return fake_session

        mock_adb_client_cls.return_value.open_persistent_shell = MagicMock(
            side_effect=fake_open_shell
        )

        device = AndroidDevice("emulator-5554")
        device.connect()
        result = device.tap(100, 200)
        assert result is True
        device.disconnect()

    @patch("play_monkey.devices.android.AdbClient")
    @patch("play_monkey.devices.android.subprocess.run")
    def test_swipe(self, mock_run, mock_adb_client_cls):
        from play_monkey.devices.android import AndroidDevice

        mock_run.return_value = MagicMock(returncode=0, stdout="device\n")

        fake_session = self._make_fake_session()

        async def fake_open_shell(serial):
            return fake_session

        mock_adb_client_cls.return_value.open_persistent_shell = MagicMock(
            side_effect=fake_open_shell
        )

        device = AndroidDevice("emulator-5554")
        device.connect()
        result = device.swipe(100, 200, 300, 400, 500)
        assert result is True
        device.disconnect()

    @patch("play_monkey.devices.android.AdbClient")
    @patch("play_monkey.devices.android.subprocess.run")
    def test_session_reused_across_commands(self, mock_run, mock_adb_client_cls):
        from play_monkey.devices.android import AndroidDevice

        mock_run.return_value = MagicMock(returncode=0, stdout="device\n")

        fake_session = self._make_fake_session()
        call_count = 0

        async def fake_open_shell(serial):
            nonlocal call_count
            call_count += 1
            return fake_session

        mock_adb_client_cls.return_value.open_persistent_shell = MagicMock(
            side_effect=fake_open_shell
        )

        device = AndroidDevice("emulator-5554")
        device.connect()
        device.tap(100, 200)
        device.tap(300, 400)
        device.swipe(100, 200, 300, 400, 500)
        device.disconnect()

        assert call_count == 1


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


