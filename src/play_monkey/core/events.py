"""Event types for monkey testing."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from ..config.models import BoundsConfig, ExclusionZone
from .bounds import random_point_in_bounds, random_point_avoiding_exclusions


class EventType(str, Enum):
    """Event type enumeration."""
    TAP = "tap"
    SWIPE = "swipe"


@dataclass
class Event(ABC):
    """Base class for all events."""

    event_type: EventType

    @abstractmethod
    def to_dict(self) -> dict:
        """Convert event to dictionary representation."""
        pass


@dataclass
class TapEvent(Event):
    """Tap event at a specific coordinate."""

    x: int
    y: int

    def __init__(self, x: int, y: int):
        super().__init__(event_type=EventType.TAP)
        self.x = x
        self.y = y

    def to_dict(self) -> dict:
        """Convert tap event to dictionary."""
        return {
            "type": self.event_type.value,
            "x": self.x,
            "y": self.y,
        }

    def __str__(self) -> str:
        return f"Tap({self.x}, {self.y})"


@dataclass
class SwipeEvent(Event):
    """Swipe event from one coordinate to another."""

    x1: int
    y1: int
    x2: int
    y2: int
    duration_ms: int

    def __init__(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        super().__init__(event_type=EventType.SWIPE)
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2
        self.duration_ms = duration_ms

    def to_dict(self) -> dict:
        """Convert swipe event to dictionary."""
        return {
            "type": self.event_type.value,
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
            "duration_ms": self.duration_ms,
        }

    def __str__(self) -> str:
        return f"Swipe({self.x1}, {self.y1}) -> ({self.x2}, {self.y2})"


class EventGenerator:
    """Generates random events based on configured ratios."""

    def __init__(
        self,
        tap_ratio: float,
        swipe_ratio: float,
        bounds: Optional[BoundsConfig],
        screen_width: int,
        screen_height: int,
        swipe_duration_min_ms: int = 100,
        swipe_duration_max_ms: int = 500,
        exclusion_zones: Optional[list[ExclusionZone]] = None,
    ):
        """Initialize event generator.

        Args:
            tap_ratio: Probability of generating tap events (0.0-1.0)
            swipe_ratio: Probability of generating swipe events (0.0-1.0)
            bounds: Coordinate bounds, or None for full screen
            screen_width: Device screen width
            screen_height: Device screen height
            swipe_duration_min_ms: Minimum swipe duration in milliseconds
            swipe_duration_max_ms: Maximum swipe duration in milliseconds
            exclusion_zones: List of forbidden areas to avoid
        """
        self.tap_ratio = tap_ratio
        self.swipe_ratio = swipe_ratio
        self.bounds = bounds
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.swipe_duration_min_ms = swipe_duration_min_ms
        self.swipe_duration_max_ms = swipe_duration_max_ms
        self.exclusion_zones = exclusion_zones or []

        # Validate ratios
        total = tap_ratio + swipe_ratio
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Event ratios must sum to 1.0, got {total}")

        if swipe_duration_min_ms <= 0 or swipe_duration_max_ms <= 0:
            raise ValueError("Swipe durations must be positive")
        if swipe_duration_min_ms > swipe_duration_max_ms:
            raise ValueError(
                f"swipe_duration_min_ms ({swipe_duration_min_ms}) must be <= "
                f"swipe_duration_max_ms ({swipe_duration_max_ms})"
            )

    def generate(self) -> Event:
        """Generate a random event based on configured ratios.

        Returns:
            A TapEvent or SwipeEvent
        """
        import random

        # Generate random number to determine event type
        rand = random.random()

        if rand < self.tap_ratio:
            return self._generate_tap()
        else:
            return self._generate_swipe()

    def _generate_tap(self) -> TapEvent:
        """Generate a random tap event."""
        if self.exclusion_zones:
            x, y = random_point_avoiding_exclusions(
                self.bounds, self.screen_width, self.screen_height, self.exclusion_zones
            )
        else:
            x, y = random_point_in_bounds(self.bounds, self.screen_width, self.screen_height)
        return TapEvent(x, y)

    def _generate_swipe(self) -> SwipeEvent:
        """Generate a random swipe event."""
        # Generate start and end points, avoiding exclusion zones
        if self.exclusion_zones:
            x1, y1 = random_point_avoiding_exclusions(
                self.bounds, self.screen_width, self.screen_height, self.exclusion_zones
            )
            x2, y2 = random_point_avoiding_exclusions(
                self.bounds, self.screen_width, self.screen_height, self.exclusion_zones
            )
        else:
            x1, y1 = random_point_in_bounds(self.bounds, self.screen_width, self.screen_height)
            x2, y2 = random_point_in_bounds(self.bounds, self.screen_width, self.screen_height)

        # Random duration within the configured range (inclusive on both ends)
        import random
        duration_ms = random.randint(self.swipe_duration_min_ms, self.swipe_duration_max_ms)

        return SwipeEvent(x1, y1, x2, y2, duration_ms)
