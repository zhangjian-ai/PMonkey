"""Base device interface for cross-platform abstraction."""

from abc import ABC, abstractmethod
from typing import Tuple


class Device(ABC):
    """Abstract base class for device implementations."""

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the device.

        Returns:
            True if connection successful, False otherwise
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the device."""
        pass

    @abstractmethod
    def get_screen_size(self) -> Tuple[int, int]:
        """Get device screen dimensions.

        Returns:
            Tuple of (width, height) in pixels
        """
        pass

    @abstractmethod
    def tap(self, x: int, y: int) -> bool:
        """Perform a tap at the specified coordinates.

        Args:
            x: X coordinate
            y: Y coordinate

        Returns:
            True if tap successful, False otherwise
        """
        pass

    @abstractmethod
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> bool:
        """Perform a swipe from one point to another.

        Args:
            x1: Start X coordinate
            y1: Start Y coordinate
            x2: End X coordinate
            y2: End Y coordinate
            duration_ms: Swipe duration in milliseconds

        Returns:
            True if swipe successful, False otherwise
        """
        pass

    @abstractmethod
    def is_app_running(self, app_identifier: str) -> bool:
        """Check if the specified app is running.

        Args:
            app_identifier: App package name (Android) or bundle ID (iOS)

        Returns:
            True if app is running, False otherwise
        """
        pass

    @abstractmethod
    def start_app(self, app_identifier: str) -> bool:
        """Start the specified app.

        Args:
            app_identifier: App package name (Android) or bundle ID (iOS)

        Returns:
            True if app started successfully, False otherwise
        """
        pass

    @abstractmethod
    def stop_app(self, app_identifier: str) -> bool:
        """Stop the specified app.

        Args:
            app_identifier: App package name (Android) or bundle ID (iOS)

        Returns:
            True if app stopped successfully, False otherwise
        """
        pass
