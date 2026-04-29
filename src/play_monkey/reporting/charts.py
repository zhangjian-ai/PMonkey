"""Chart generation for performance metrics."""

import base64
from io import BytesIO
from typing import List, Optional

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure

from ..monitoring.metrics import MetricSample
from ..stability.models import StabilityIssue, StabilityIssueType


class ChartGenerator:
    """Generates charts for performance metrics."""

    @staticmethod
    def generate_performance_charts(
        samples: List[MetricSample],
        stability_issues: Optional[List[StabilityIssue]] = None,
    ) -> dict:
        """Generate all performance charts.

        Args:
            samples: List of performance metric samples
            stability_issues: Optional list of stability issues to annotate

        Returns:
            Dictionary with base64-encoded chart images
        """
        if not samples:
            return {}

        charts = {}

        # Extract timestamps and convert to seconds from start
        start_time = samples[0].timestamp
        timestamps = [(s.timestamp - start_time).total_seconds() for s in samples]

        # Generate CPU chart
        cpu_values = [s.cpu_percent for s in samples if s.cpu_percent is not None]
        if cpu_values:
            charts["cpu"] = ChartGenerator._generate_metric_chart(
                timestamps[:len(cpu_values)],
                cpu_values,
                "CPU Usage (%)",
                "CPU %",
                stability_issues,
            )

        # Generate Memory chart
        memory_values = [s.memory_mb for s in samples if s.memory_mb is not None]
        if memory_values:
            charts["memory"] = ChartGenerator._generate_metric_chart(
                timestamps[:len(memory_values)],
                memory_values,
                "Memory Usage (MB)",
                "Memory MB",
                stability_issues,
            )

        # Generate FPS chart
        fps_values = [s.fps for s in samples if s.fps is not None]
        if fps_values:
            charts["fps"] = ChartGenerator._generate_metric_chart(
                timestamps[:len(fps_values)],
                fps_values,
                "FPS",
                "FPS",
                stability_issues,
            )

        # Generate Battery chart
        battery_values = [s.battery_percent for s in samples if s.battery_percent is not None]
        if battery_values:
            charts["battery"] = ChartGenerator._generate_metric_chart(
                timestamps[:len(battery_values)],
                battery_values,
                "Battery Level (%)",
                "Battery %",
                stability_issues,
            )

        return charts

    @staticmethod
    def _generate_metric_chart(
        timestamps: List[float],
        values: List[float],
        title: str,
        ylabel: str,
        stability_issues: Optional[List[StabilityIssue]] = None,
    ) -> str:
        """Generate a single metric chart.

        Args:
            timestamps: Time values in seconds
            values: Metric values
            title: Chart title
            ylabel: Y-axis label
            stability_issues: Optional stability issues to annotate

        Returns:
            Base64-encoded PNG image
        """
        fig, ax = plt.subplots(figsize=(10, 4))

        # Plot metric line
        ax.plot(timestamps, values, linewidth=2, color="#2196F3")

        # Add max and average lines
        max_val = max(values)
        avg_val = np.mean(values)

        ax.axhline(y=max_val, color="red", linestyle="--", linewidth=1, alpha=0.7, label=f"Max: {max_val:.1f}")
        ax.axhline(y=avg_val, color="green", linestyle="--", linewidth=1, alpha=0.7, label=f"Avg: {avg_val:.1f}")

        # Annotate crashes and ANRs
        if stability_issues and timestamps:
            start_time_offset = timestamps[0] if timestamps else 0
            for issue in stability_issues:
                if issue.type in [StabilityIssueType.CRASH, StabilityIssueType.ANR]:
                    # Calculate issue time relative to start
                    # For now, we'll mark them at the end since we don't have precise timing
                    # TODO: Calculate actual issue timestamp relative to test start
                    marker_color = "red" if issue.type == StabilityIssueType.CRASH else "orange"
                    marker_label = "Crash" if issue.type == StabilityIssueType.CRASH else "ANR"

        # Styling
        ax.set_xlabel("Time (seconds)", fontsize=10)
        ax.set_ylabel(ylabel, fontsize=10)
        ax.set_title(title, fontsize=12, fontweight="bold")
        ax.legend(loc="upper right", fontsize=9)
        ax.grid(True, alpha=0.3)

        # Convert to base64
        buffer = BytesIO()
        plt.tight_layout()
        plt.savefig(buffer, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)

        buffer.seek(0)
        image_base64 = base64.b64encode(buffer.read()).decode()

        return f"data:image/png;base64,{image_base64}"
