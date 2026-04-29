"""Base interface for stability monitoring."""

from abc import ABC, abstractmethod
from typing import List

from .models import StabilityIssue, StabilityReport, StabilityStatus


class StabilityMonitor(ABC):
    """Abstract base class for stability monitoring."""

    @abstractmethod
    def start_monitoring(self, app_identifier: str) -> None:
        """Start monitoring the specified app.

        Args:
            app_identifier: App package name (Android) or bundle ID (iOS)
        """
        pass

    @abstractmethod
    def check_stability(self) -> StabilityStatus:
        """Check current stability status.

        Returns:
            Current StabilityStatus
        """
        pass

    @abstractmethod
    def get_issues(self) -> List[StabilityIssue]:
        """Get all detected stability issues.

        Returns:
            List of StabilityIssue instances
        """
        pass

    @abstractmethod
    def stop_monitoring(self) -> StabilityReport:
        """Stop monitoring and return complete report.

        Returns:
            Complete StabilityReport
        """
        pass
