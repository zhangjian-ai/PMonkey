"""Statistics calculation utilities."""

from typing import List

import numpy as np


class Statistics:
    """Statistical calculations for performance metrics."""

    @staticmethod
    def compute_max(values: List[float]) -> float:
        """Compute maximum value."""
        if not values:
            return 0.0
        return float(np.max(values))

    @staticmethod
    def compute_min(values: List[float]) -> float:
        """Compute minimum value."""
        if not values:
            return 0.0
        return float(np.min(values))

    @staticmethod
    def compute_average(values: List[float]) -> float:
        """Compute average value."""
        if not values:
            return 0.0
        return float(np.mean(values))

    @staticmethod
    def compute_percentile(values: List[float], percentile: int) -> float:
        """Compute percentile value.

        Args:
            values: List of values
            percentile: Percentile to compute (0-100)

        Returns:
            Percentile value
        """
        if not values:
            return 0.0
        return float(np.percentile(values, percentile))

    @staticmethod
    def compute_all_stats(values: List[float]) -> dict:
        """Compute all statistics for a metric.

        Returns:
            Dictionary with max, min, avg, and percentiles
        """
        if not values:
            return {
                "max": 0.0,
                "min": 0.0,
                "avg": 0.0,
                "p50": 0.0,
                "p90": 0.0,
                "p95": 0.0,
                "p99": 0.0,
            }

        return {
            "max": Statistics.compute_max(values),
            "min": Statistics.compute_min(values),
            "avg": Statistics.compute_average(values),
            "p50": Statistics.compute_percentile(values, 50),
            "p90": Statistics.compute_percentile(values, 90),
            "p95": Statistics.compute_percentile(values, 95),
            "p99": Statistics.compute_percentile(values, 99),
        }
