"""Base interface for performance monitoring."""

from abc import ABC, abstractmethod
from typing import List

from .metrics import MetricSample


class PerformanceMonitor(ABC):
    """Abstract base class for performance monitoring."""

    @abstractmethod
    def start_monitoring(self, app_identifier: str) -> None:
        """Start monitoring the specified app.

        Args:
            app_identifier: App package name (Android) or bundle ID (iOS)
        """
        pass

    @abstractmethod
    def collect_sample(self) -> MetricSample:
        """Collect a single performance metric sample.

        Returns:
            MetricSample with current performance data
        """
        pass

    @abstractmethod
    def stop_monitoring(self) -> List[MetricSample]:
        """Stop monitoring and return all collected samples.

        Returns:
            List of all collected MetricSample instances
        """
        pass
