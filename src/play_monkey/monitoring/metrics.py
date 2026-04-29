"""Performance monitoring data models."""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class MetricSample:
    """Single performance metric sample at a point in time."""

    timestamp: datetime
    cpu_percent: Optional[float] = None
    memory_mb: Optional[float] = None
    fps: Optional[float] = None
    battery_percent: Optional[float] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "cpu_percent": self.cpu_percent,
            "memory_mb": self.memory_mb,
            "fps": self.fps,
            "battery_percent": self.battery_percent,
        }


@dataclass
class MetricTimeSeries:
    """Time series data for a specific metric."""

    metric_name: str
    samples: List[MetricSample]

    def get_values(self, metric_key: str) -> List[float]:
        """Extract values for a specific metric.

        Args:
            metric_key: Metric key (cpu_percent, memory_mb, fps, battery_percent)

        Returns:
            List of non-None values
        """
        values = []
        for sample in self.samples:
            value = getattr(sample, metric_key, None)
            if value is not None:
                values.append(value)
        return values

    def get_timestamps(self) -> List[datetime]:
        """Get all timestamps."""
        return [sample.timestamp for sample in self.samples]
