"""Coordinate bounds utilities."""

import random
from typing import Optional, Tuple

from ..config.models import BoundsConfig


def random_point_in_bounds(
    bounds: Optional[BoundsConfig],
    screen_width: int,
    screen_height: int
) -> Tuple[int, int]:
    """Generate a random point within the specified bounds.

    Args:
        bounds: Coordinate bounds configuration, or None for full screen
        screen_width: Device screen width
        screen_height: Device screen height

    Returns:
        Tuple of (x, y) coordinates
    """
    if bounds is None:
        # Use full screen
        x = random.randint(0, screen_width - 1)
        y = random.randint(0, screen_height - 1)
    else:
        # Use specified bounds
        x = random.randint(bounds.x_min, min(bounds.x_max, screen_width - 1))
        y = random.randint(bounds.y_min, min(bounds.y_max, screen_height - 1))

    return (x, y)


def validate_point_in_bounds(
    x: int,
    y: int,
    bounds: Optional[BoundsConfig],
    screen_width: int,
    screen_height: int
) -> bool:
    """Validate that a point is within bounds.

    Args:
        x: X coordinate
        y: Y coordinate
        bounds: Coordinate bounds configuration, or None for full screen
        screen_width: Device screen width
        screen_height: Device screen height

    Returns:
        True if point is within bounds
    """
    # Check screen bounds
    if x < 0 or x >= screen_width or y < 0 or y >= screen_height:
        return False

    # Check configured bounds if specified
    if bounds is not None:
        if x < bounds.x_min or x > bounds.x_max:
            return False
        if y < bounds.y_min or y > bounds.y_max:
            return False

    return True
