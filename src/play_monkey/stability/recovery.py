"""Recovery strategies for handling crashes and ANRs."""

import time
from typing import Optional

from ..devices.base import Device


class RecoveryStrategy:
    """Handles recovery from crashes and ANRs."""

    def __init__(self, device: Device, app_identifier: str):
        """Initialize recovery strategy.

        Args:
            device: Device instance
            app_identifier: App package name or bundle ID
        """
        self.device = device
        self.app_identifier = app_identifier
        self.restart_count = 0
        self.max_restart_attempts = 3

    def handle_crash(self) -> bool:
        """Handle application crash.

        Returns:
            True if recovery successful, False otherwise
        """
        self.restart_count += 1

        if self.restart_count > self.max_restart_attempts:
            print(f"Max restart attempts ({self.max_restart_attempts}) reached")
            return False

        print(f"App crashed. Attempting restart ({self.restart_count}/{self.max_restart_attempts})...")

        # Stop the app if it's still running
        if self.device.is_app_running(self.app_identifier):
            self.device.stop_app(self.app_identifier)
            time.sleep(1)

        # Start the app
        success = self.device.start_app(self.app_identifier)

        if success:
            # Wait for app to start
            time.sleep(2)

            # Verify app is running
            if self.device.is_app_running(self.app_identifier):
                print("App restarted successfully")
                return True
            else:
                print("App failed to start after restart")
                return False
        else:
            print("Failed to restart app")
            return False

    def handle_anr(self) -> bool:
        """Handle ANR (Application Not Responding).

        Returns:
            True if recovery successful, False otherwise
        """
        print("ANR detected. Attempting recovery...")

        # For Android, try to dismiss ANR dialog by pressing back button
        # This is a simplified approach
        # TODO: Implement more sophisticated ANR handling

        # Wait a moment for ANR dialog to appear
        time.sleep(1)

        # Check if app is still running
        if not self.device.is_app_running(self.app_identifier):
            # App was killed by ANR, restart it
            return self.handle_crash()

        print("ANR recovery completed")
        return True

    def reset_restart_count(self) -> None:
        """Reset the restart counter."""
        self.restart_count = 0
