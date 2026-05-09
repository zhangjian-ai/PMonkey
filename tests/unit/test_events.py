"""Unit tests for event generation and bounds logic."""

import pytest

from play_monkey.config.models import BoundsConfig
from play_monkey.core.bounds import random_point_in_bounds, validate_point_in_bounds
from play_monkey.core.events import EventGenerator, EventType, SwipeEvent, TapEvent


class TestBounds:
    """Tests for bounds utilities."""

    def test_random_point_full_screen(self):
        """Test random point generation on full screen."""
        x, y = random_point_in_bounds(None, 1080, 1920)
        assert 0 <= x < 1080
        assert 0 <= y < 1920

    def test_random_point_with_bounds(self):
        """Test random point generation within bounds."""
        bounds = BoundsConfig(x_min=100, x_max=900, y_min=200, y_max=1800)
        x, y = random_point_in_bounds(bounds, 1080, 1920)
        assert 100 <= x <= 900
        assert 200 <= y <= 1800

    def test_validate_point_in_screen(self):
        """Test point validation within screen."""
        assert validate_point_in_bounds(500, 1000, None, 1080, 1920)
        assert not validate_point_in_bounds(-1, 1000, None, 1080, 1920)
        assert not validate_point_in_bounds(1080, 1000, None, 1080, 1920)

    def test_validate_point_in_bounds(self):
        """Test point validation within configured bounds."""
        bounds = BoundsConfig(x_min=100, x_max=900, y_min=200, y_max=1800)
        assert validate_point_in_bounds(500, 1000, bounds, 1080, 1920)
        assert not validate_point_in_bounds(50, 1000, bounds, 1080, 1920)
        assert not validate_point_in_bounds(500, 100, bounds, 1080, 1920)


class TestTapEvent:
    """Tests for TapEvent."""

    def test_create_tap_event(self):
        """Test creating a tap event."""
        event = TapEvent(100, 200)
        assert event.event_type == EventType.TAP
        assert event.x == 100
        assert event.y == 200

    def test_tap_event_to_dict(self):
        """Test converting tap event to dictionary."""
        event = TapEvent(100, 200)
        data = event.to_dict()
        assert data["type"] == "tap"
        assert data["x"] == 100
        assert data["y"] == 200


class TestSwipeEvent:
    """Tests for SwipeEvent."""

    def test_create_swipe_event(self):
        """Test creating a swipe event."""
        event = SwipeEvent(100, 200, 300, 400, 250)
        assert event.event_type == EventType.SWIPE
        assert event.x1 == 100
        assert event.y1 == 200
        assert event.x2 == 300
        assert event.y2 == 400
        assert event.duration_ms == 250

    def test_swipe_event_default_duration(self):
        """Test swipe event with default duration."""
        event = SwipeEvent(100, 200, 300, 400)
        assert event.duration_ms == 300

    def test_swipe_event_to_dict(self):
        """Test converting swipe event to dictionary."""
        event = SwipeEvent(100, 200, 300, 400, 250)
        data = event.to_dict()
        assert data["type"] == "swipe"
        assert data["x1"] == 100
        assert data["y1"] == 200
        assert data["x2"] == 300
        assert data["y2"] == 400
        assert data["duration_ms"] == 250


class TestEventGenerator:
    """Tests for EventGenerator."""

    def test_create_generator(self):
        """Test creating an event generator."""
        generator = EventGenerator(0.7, 0.3, None, 1080, 1920)
        assert generator.tap_ratio == 0.7
        assert generator.swipe_ratio == 0.3

    def test_generator_validates_ratios(self):
        """Test that generator validates ratio sum."""
        with pytest.raises(ValueError, match="must sum to 1.0"):
            EventGenerator(0.5, 0.3, None, 1080, 1920)

    def test_generate_events(self):
        """Test generating random events."""
        generator = EventGenerator(0.7, 0.3, None, 1080, 1920)

        # Generate multiple events and check distribution
        tap_count = 0
        swipe_count = 0
        total = 100

        for _ in range(total):
            event = generator.generate()
            if isinstance(event, TapEvent):
                tap_count += 1
            elif isinstance(event, SwipeEvent):
                swipe_count += 1

        # Check that we got both types
        assert tap_count > 0
        assert swipe_count > 0
        assert tap_count + swipe_count == total

    def test_generate_with_bounds(self):
        """Test generating events within bounds."""
        bounds = BoundsConfig(x_min=100, x_max=900, y_min=200, y_max=1800)
        generator = EventGenerator(0.5, 0.5, bounds, 1080, 1920)

        for _ in range(20):
            event = generator.generate()
            if isinstance(event, TapEvent):
                assert 100 <= event.x <= 900
                assert 200 <= event.y <= 1800
            elif isinstance(event, SwipeEvent):
                assert 100 <= event.x1 <= 900
                assert 200 <= event.y1 <= 1800
                assert 100 <= event.x2 <= 900
                assert 200 <= event.y2 <= 1800

    def test_all_tap_events(self):
        """Test generator with 100% tap ratio."""
        generator = EventGenerator(1.0, 0.0, None, 1080, 1920)

        for _ in range(10):
            event = generator.generate()
            assert isinstance(event, TapEvent)

    def test_all_swipe_events(self):
        """Test generator with 100% swipe ratio."""
        generator = EventGenerator(0.0, 1.0, None, 1080, 1920)

        for _ in range(10):
            event = generator.generate()
            assert isinstance(event, SwipeEvent)

    def test_swipe_duration_respects_config(self):
        """Swipe duration must fall within the configured range."""
        generator = EventGenerator(
            0.0, 1.0, None, 1080, 1920,
            swipe_duration_min_ms=250,
            swipe_duration_max_ms=400,
        )

        for _ in range(50):
            event = generator.generate()
            assert isinstance(event, SwipeEvent)
            assert 250 <= event.duration_ms <= 400

    def test_swipe_duration_fixed_when_min_equals_max(self):
        """When min_ms == max_ms, every swipe uses that exact duration."""
        generator = EventGenerator(
            0.0, 1.0, None, 1080, 1920,
            swipe_duration_min_ms=300,
            swipe_duration_max_ms=300,
        )

        for _ in range(20):
            event = generator.generate()
            assert event.duration_ms == 300

    def test_swipe_duration_rejects_invalid_range(self):
        """Generator must reject min > max."""
        with pytest.raises(ValueError):
            EventGenerator(
                0.0, 1.0, None, 1080, 1920,
                swipe_duration_min_ms=500,
                swipe_duration_max_ms=200,
            )
