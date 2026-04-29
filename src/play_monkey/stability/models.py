"""Stability monitoring data models."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional


class StabilityIssueType(str, Enum):
    """Type of stability issue."""
    CRASH = "crash"
    ANR = "anr"
    ERROR = "error"
    HANG = "hang"


@dataclass
class StabilityIssue:
    """A single stability issue (crash, ANR, error, etc.)."""

    type: StabilityIssueType
    timestamp: datetime
    message: str
    stacktrace: Optional[str] = None
    severity: str = "medium"  # critical, high, medium, low

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "type": self.type.value,
            "timestamp": self.timestamp.isoformat(),
            "message": self.message,
            "stacktrace": self.stacktrace,
            "severity": self.severity,
        }


@dataclass
class StabilityStatus:
    """Current stability status."""

    is_running: bool
    has_crashed: bool
    has_anr: bool
    error_count: int


@dataclass
class StabilityReport:
    """Complete stability report."""

    total_crashes: int
    total_anrs: int
    total_errors: int
    issues: List[StabilityIssue]
    crash_rate: float = 0.0  # crashes per 1000 events
    anr_rate: float = 0.0    # ANRs per 1000 events

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "total_crashes": self.total_crashes,
            "total_anrs": self.total_anrs,
            "total_errors": self.total_errors,
            "crash_rate": self.crash_rate,
            "anr_rate": self.anr_rate,
            "issues": [issue.to_dict() for issue in self.issues],
        }
