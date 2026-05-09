"""Unit tests for configuration models."""

import pytest
from pydantic import ValidationError

from play_monkey.config.models import (
    BoundsConfig,
    EventRatios,
    MonitoringConfig,
    Platform,
    StabilityConfig,
    SwipeDurationConfig,
    TestConfig,
)


class TestEventRatios:
    """Tests for EventRatios model."""

    def test_valid_ratios(self):
        """Test valid event ratios."""
        ratios = EventRatios(tap=0.7, swipe=0.3)
        assert ratios.tap == 0.7
        assert ratios.swipe == 0.3

    def test_ratios_must_sum_to_one(self):
        """Test that ratios must sum to 1.0."""
        with pytest.raises(ValidationError, match="must sum to 1.0"):
            EventRatios(tap=0.5, swipe=0.3)

    def test_ratios_within_range(self):
        """Test that ratios must be between 0 and 1."""
        with pytest.raises(ValidationError):
            EventRatios(tap=1.5, swipe=-0.5)

    def test_edge_case_all_tap(self):
        """Test edge case with all tap events."""
        ratios = EventRatios(tap=1.0, swipe=0.0)
        assert ratios.tap == 1.0
        assert ratios.swipe == 0.0

    def test_edge_case_all_swipe(self):
        """Test edge case with all swipe events."""
        ratios = EventRatios(tap=0.0, swipe=1.0)
        assert ratios.tap == 0.0
        assert ratios.swipe == 1.0


class TestBoundsConfig:
    """Tests for BoundsConfig model."""

    def test_valid_bounds(self):
        """Test valid coordinate bounds."""
        bounds = BoundsConfig(x_min=0, x_max=1080, y_min=0, y_max=1920)
        assert bounds.x_min == 0
        assert bounds.x_max == 1080
        assert bounds.y_min == 0
        assert bounds.y_max == 1920

    def test_x_min_must_be_less_than_x_max(self):
        """Test that x_min must be less than x_max."""
        with pytest.raises(ValidationError):
            BoundsConfig(x_min=500, x_max=100, y_min=0, y_max=1920)

    def test_y_min_must_be_less_than_y_max(self):
        """Test that y_min must be less than y_max."""
        with pytest.raises(ValidationError):
            BoundsConfig(x_min=0, x_max=1080, y_min=1000, y_max=200)

    def test_negative_coordinates_not_allowed(self):
        """Test that negative coordinates are not allowed."""
        with pytest.raises(ValidationError):
            BoundsConfig(x_min=-100, x_max=1080, y_min=0, y_max=1920)


class TestMonitoringConfig:
    """Tests for MonitoringConfig model."""

    def test_default_config(self):
        """Test default monitoring configuration."""
        config = MonitoringConfig()
        assert config.enabled is True
        assert config.sample_interval_seconds == 2.0
        assert "cpu" in config.metrics
        assert "memory" in config.metrics

    def test_custom_config(self):
        """Test custom monitoring configuration."""
        config = MonitoringConfig(
            enabled=False,
            sample_interval_seconds=1.0,
            metrics=["cpu", "memory"]
        )
        assert config.enabled is False
        assert config.sample_interval_seconds == 1.0
        assert len(config.metrics) == 2


class TestStabilityConfig:
    """Tests for StabilityConfig model."""

    def test_default_config(self):
        """Test default stability configuration."""
        config = StabilityConfig()
        assert config.monitor_crashes is True
        assert config.monitor_anr is True
        assert config.monitor_errors is True
        assert config.continue_on_crash is True
        assert config.continue_on_anr is True
        assert config.anr_threshold_seconds == 5.0

    def test_custom_config(self):
        """Test custom stability configuration."""
        config = StabilityConfig(
            monitor_crashes=False,
            continue_on_crash=False,
            max_crash_count=3
        )
        assert config.monitor_crashes is False
        assert config.continue_on_crash is False
        assert config.max_crash_count == 3


class TestSwipeDurationConfig:
    """Tests for SwipeDurationConfig model."""

    def test_default_config(self):
        config = SwipeDurationConfig()
        assert config.min_ms == 100
        assert config.max_ms == 500

    def test_custom_config(self):
        config = SwipeDurationConfig(min_ms=50, max_ms=200)
        assert config.min_ms == 50
        assert config.max_ms == 200

    def test_fixed_duration(self):
        config = SwipeDurationConfig(min_ms=300, max_ms=300)
        assert config.min_ms == 300
        assert config.max_ms == 300

    def test_min_must_not_exceed_max(self):
        with pytest.raises(ValidationError):
            SwipeDurationConfig(min_ms=800, max_ms=200)

    def test_positive_only(self):
        with pytest.raises(ValidationError):
            SwipeDurationConfig(min_ms=0, max_ms=300)
        with pytest.raises(ValidationError):
            SwipeDurationConfig(min_ms=-10, max_ms=300)

    def test_default_config(self):
        """Test default stability configuration."""
        config = StabilityConfig()
        assert config.monitor_crashes is True
        assert config.monitor_anr is True
        assert config.monitor_errors is True
        assert config.continue_on_crash is True
        assert config.continue_on_anr is True
        assert config.anr_threshold_seconds == 5.0

    def test_custom_config(self):
        """Test custom stability configuration."""
        config = StabilityConfig(
            monitor_crashes=False,
            continue_on_crash=False,
            max_crash_count=3
        )
        assert config.monitor_crashes is False
        assert config.continue_on_crash is False
        assert config.max_crash_count == 3


class TestTestConfig:
    """Tests for TestConfig model."""

    def test_valid_config_with_event_count(self):
        """Test valid configuration with event count."""
        config = TestConfig(
            platform=Platform.ANDROID,
            device_id="emulator-5554",
            app_package="com.example.app",
            event_ratios=EventRatios(tap=0.7, swipe=0.3),
            event_count=1000,
        )
        assert config.platform == Platform.ANDROID
        assert config.device_id == "emulator-5554"
        assert config.event_count == 1000

    def test_valid_config_with_duration(self):
        """Test valid configuration with duration."""
        config = TestConfig(
            platform=Platform.IOS,
            device_id="iPhone-12",
            app_package="com.example.app",
            event_ratios=EventRatios(tap=0.5, swipe=0.5),
            duration_seconds=60,
        )
        assert config.platform == Platform.IOS
        assert config.duration_seconds == 60

    def test_must_specify_termination_condition(self):
        """Test that at least one termination condition must be specified."""
        with pytest.raises(ValidationError, match="Must specify either event_count or duration_seconds"):
            TestConfig(
                platform=Platform.ANDROID,
                device_id="emulator-5554",
                app_package="com.example.app",
                event_ratios=EventRatios(tap=0.7, swipe=0.3),
            )

    def test_event_count_takes_priority(self):
        """Test that event_count takes priority when both are specified."""
        config = TestConfig(
            platform=Platform.ANDROID,
            device_id="emulator-5554",
            app_package="com.example.app",
            event_ratios=EventRatios(tap=0.7, swipe=0.3),
            event_count=1000,
            duration_seconds=60,
        )
        # Both are stored, but scheduler should prioritize event_count
        assert config.event_count == 1000
        assert config.duration_seconds == 60

    def test_default_monitoring_and_stability(self):
        """Test that monitoring and stability configs have defaults."""
        config = TestConfig(
            platform=Platform.ANDROID,
            device_id="emulator-5554",
            app_package="com.example.app",
            event_ratios=EventRatios(tap=0.7, swipe=0.3),
            event_count=1000,
        )
        assert config.monitoring.enabled is True
        assert config.stability.monitor_crashes is True
