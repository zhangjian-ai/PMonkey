"""Monkey test scheduler and orchestrator."""

import time
from datetime import datetime
from typing import Optional

from ..config.models import TestConfig
from .events import Event, EventGenerator


class MonkeyScheduler:
    """Main scheduler for monkey testing.

    Coordinates event generation, device execution, and monitoring.
    """

    def __init__(
        self,
        config: TestConfig,
        event_generator: EventGenerator,
    ):
        """Initialize scheduler.

        Args:
            config: Test configuration
            event_generator: Event generator instance
        """
        self.config = config
        self.event_generator = event_generator

        # Statistics
        self.events_executed = 0
        self.events_failed = 0
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None

        # Stability tracking
        self.crash_count = 0
        self.anr_count = 0

    def should_continue(self) -> bool:
        """Check if test should continue.

        Returns:
            True if test should continue, False otherwise
        """
        # Check crash limit
        if self.config.stability.max_crash_count is not None:
            if self.crash_count >= self.config.stability.max_crash_count:
                return False

        # Check event count (priority)
        if self.config.event_count is not None:
            return self.events_executed < self.config.event_count

        # Check duration
        if self.config.duration_seconds is not None:
            if self.start_time is None:
                return True
            elapsed = (datetime.now() - self.start_time).total_seconds()
            return elapsed < self.config.duration_seconds

        return False

    def get_progress(self) -> dict:
        """Get current test progress.

        Returns:
            Dictionary with progress information
        """
        progress = {
            "events_executed": self.events_executed,
            "events_failed": self.events_failed,
            "crash_count": self.crash_count,
            "anr_count": self.anr_count,
        }

        if self.start_time:
            elapsed = (datetime.now() - self.start_time).total_seconds()
            progress["elapsed_seconds"] = elapsed

        # Calculate remaining
        if self.config.event_count is not None:
            remaining = self.config.event_count - self.events_executed
            progress["events_remaining"] = remaining
            progress["total_events"] = self.config.event_count
        elif self.config.duration_seconds is not None and self.start_time:
            elapsed = (datetime.now() - self.start_time).total_seconds()
            remaining = max(0, self.config.duration_seconds - elapsed)
            progress["seconds_remaining"] = remaining
            progress["total_seconds"] = self.config.duration_seconds

        return progress

    def execute_event(self, event: Event, device) -> bool:
        """Execute a single event on the device.

        Args:
            event: Event to execute
            device: Device instance

        Returns:
            True if event executed successfully, False otherwise
        """
        try:
            from .events import EventType

            if event.event_type == EventType.TAP:
                return device.tap(event.x, event.y)
            elif event.event_type == EventType.SWIPE:
                return device.swipe(event.x1, event.y1, event.x2, event.y2, event.duration_ms)
            else:
                return False
        except Exception:
            return False

    def run(self, device, performance_monitor=None, stability_monitor=None, recovery_strategy=None):
        """Run the monkey test.

        Args:
            device: Device instance to execute events on
            performance_monitor: Optional performance monitor
            stability_monitor: Optional stability monitor
            recovery_strategy: Optional recovery strategy for handling crashes/ANRs

        Returns:
            Test results dictionary
        """
        self.start_time = datetime.now()

        # Start monitoring
        if performance_monitor:
            performance_monitor.start_monitoring(self.config.app_package)
        if stability_monitor:
            stability_monitor.start_monitoring(self.config.app_package)

        try:
            while self.should_continue():
                # Generate event
                event = self.event_generator.generate()

                # Execute event
                success = self.execute_event(event, device)

                if success:
                    self.events_executed += 1
                else:
                    self.events_failed += 1

                # Check stability
                if stability_monitor:
                    status = stability_monitor.check_stability()

                    # Handle crash
                    if status.has_crashed:
                        self.crash_count += 1
                        if self.config.stability.continue_on_crash and recovery_strategy:
                            # Attempt to recover from crash
                            recovered = recovery_strategy.handle_crash()
                            if not recovered:
                                print("Failed to recover from crash, stopping test")
                                break
                        elif not self.config.stability.continue_on_crash:
                            print("Crash detected, stopping test")
                            break

                    # Handle ANR
                    if status.has_anr:
                        self.anr_count += 1
                        if self.config.stability.continue_on_anr and recovery_strategy:
                            # Attempt to recover from ANR
                            recovered = recovery_strategy.handle_anr()
                            if not recovered:
                                print("Failed to recover from ANR, stopping test")
                                break
                        elif not self.config.stability.continue_on_anr:
                            print("ANR detected, stopping test")
                            break

                # Wait for interval
                time.sleep(self.config.interval_ms / 1000.0)

        finally:
            self.end_time = datetime.now()

            # Stop monitoring
            if performance_monitor:
                performance_monitor.stop_monitoring()
            if stability_monitor:
                stability_monitor.stop_monitoring()

        # Return results
        return {
            "events_executed": self.events_executed,
            "events_failed": self.events_failed,
            "crash_count": self.crash_count,
            "anr_count": self.anr_count,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": (self.end_time - self.start_time).total_seconds(),
        }
