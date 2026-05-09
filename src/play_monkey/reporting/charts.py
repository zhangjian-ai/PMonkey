"""Chart data preparation for frontend rendering."""

from typing import List, Optional

from ..monitoring.metrics import MetricSample
from ..stability.models import StabilityIssue, StabilityIssueType


class ChartGenerator:
    """Prepares chart data for frontend JavaScript rendering."""

    @staticmethod
    def generate_performance_charts(
        samples: List[MetricSample],
        stability_issues: Optional[List[StabilityIssue]] = None,
    ) -> dict:
        if not samples:
            return {}

        charts = {}

        # CPU: dual line (app + sys)
        cpu_app_data = [
            (s.timestamp.strftime("%H:%M:%S"), s.cpu_percent)
            for s in samples if s.cpu_percent is not None
        ]
        cpu_sys_data = [
            (s.timestamp.strftime("%H:%M:%S"), s.sys_cpu_percent)
            for s in samples if s.sys_cpu_percent is not None
        ]
        if cpu_app_data:
            charts["cpu"] = ChartGenerator._prepare_dual_chart_data(
                cpu_app_data, cpu_sys_data,
                "App CPU (%)", "System CPU (%)",
                "#2196F3", "#90CAF9",
            )

        mem_data = [
            (s.timestamp.strftime("%H:%M:%S"), s.memory_mb)
            for s in samples if s.memory_mb is not None
        ]
        if mem_data:
            charts["memory"] = ChartGenerator._prepare_chart_data(
                mem_data, "Memory Usage (MB)", "#4CAF50"
            )

        fps_data = [
            (s.timestamp.strftime("%H:%M:%S"), s.fps)
            for s in samples if s.fps is not None
        ]
        if fps_data:
            charts["fps"] = ChartGenerator._prepare_chart_data(
                fps_data, "FPS", "#FF9800"
            )

        temp_data = [
            (s.timestamp.strftime("%H:%M:%S"), s.temperature)
            for s in samples if s.temperature is not None
        ]
        if temp_data:
            charts["temperature"] = ChartGenerator._prepare_chart_data(
                temp_data, "Temperature (°C)", "#F44336"
            )

        return charts

    @staticmethod
    def _prepare_chart_data(
        data: List[tuple],
        label: str,
        color: str,
    ) -> dict:
        """Prepare chart data for Chart.js.

        Args:
            data: List of (timestamp_str, value) tuples
            label: Data series label
            color: Line color
        """
        if not data:
            return {}

        labels = [d[0] for d in data]
        values = [round(d[1], 2) for d in data]

        max_val = max(values)
        min_val = min(values)
        avg_val = sum(values) / len(values)

        return {
            "labels": labels,
            "datasets": [
                {
                    "label": label,
                    "data": values,
                    "borderColor": color,
                    "backgroundColor": f"{color}20",
                    "borderWidth": 2,
                    "fill": True,
                    "tension": 0.4,
                    "pointRadius": 0,
                    "pointHoverRadius": 4,
                }
            ],
            "stats": {
                "max": round(max_val, 2),
                "min": round(min_val, 2),
                "avg": round(avg_val, 2),
            },
        }

    @staticmethod
    def _prepare_dual_chart_data(
        app_data: List[tuple],
        sys_data: List[tuple],
        app_label: str,
        sys_label: str,
        app_color: str,
        sys_color: str,
    ) -> dict:
        """Prepare dual-line chart data (app + sys) for Chart.js.

        Stats are computed from app_data only.
        """
        if not app_data:
            return {}

        labels = [d[0] for d in app_data]
        app_values = [round(d[1], 2) for d in app_data]

        # Align sys_data to the same label set (use None for missing points)
        sys_map = {d[0]: round(d[1], 2) for d in sys_data}
        sys_values = [sys_map.get(t) for t in labels]

        max_val = max(app_values)
        min_val = min(app_values)
        avg_val = sum(app_values) / len(app_values)

        datasets = [
            {
                "label": app_label,
                "data": app_values,
                "borderColor": app_color,
                "backgroundColor": f"{app_color}20",
                "borderWidth": 2,
                "fill": False,
                "tension": 0.4,
                "pointRadius": 0,
                "pointHoverRadius": 4,
            },
        ]

        if any(v is not None for v in sys_values):
            datasets.append({
                "label": sys_label,
                "data": sys_values,
                "borderColor": sys_color,
                "backgroundColor": f"{sys_color}10",
                "borderWidth": 1.5,
                "borderDash": [4, 2],
                "fill": False,
                "tension": 0.4,
                "pointRadius": 0,
                "pointHoverRadius": 4,
            })

        return {
            "labels": labels,
            "datasets": datasets,
            "stats": {
                "max": round(max_val, 2),
                "min": round(min_val, 2),
                "avg": round(avg_val, 2),
            },
        }
