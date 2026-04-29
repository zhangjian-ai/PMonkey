"""Configuration validation utilities."""

from pathlib import Path
from typing import Optional

import yaml

from .models import TestConfig


class ConfigValidator:
    """Validates test configuration."""

    @staticmethod
    def load_from_file(config_path: str) -> TestConfig:
        """Load and validate configuration from YAML file.

        Args:
            config_path: Path to YAML configuration file

        Returns:
            Validated TestConfig instance

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(path, "r") as f:
            config_dict = yaml.safe_load(f)

        return TestConfig(**config_dict)

    @staticmethod
    def validate_bounds_within_screen(
        bounds: Optional[object],
        screen_width: int,
        screen_height: int
    ) -> bool:
        """Validate that bounds are within screen dimensions.

        Args:
            bounds: BoundsConfig instance or None
            screen_width: Device screen width
            screen_height: Device screen height

        Returns:
            True if bounds are valid or None

        Raises:
            ValueError: If bounds exceed screen dimensions
        """
        if bounds is None:
            return True

        if bounds.x_max > screen_width:
            raise ValueError(
                f"x_max ({bounds.x_max}) exceeds screen width ({screen_width})"
            )
        if bounds.y_max > screen_height:
            raise ValueError(
                f"y_max ({bounds.y_max}) exceeds screen height ({screen_height})"
            )

        return True
