"""Report generator for monkey test results."""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, FileSystemLoader

from ..monitoring.metrics import MetricSample
from ..stability.models import StabilityReport
from .charts import ChartGenerator
from .statistics import Statistics


class ReportGenerator:
    """Generates test reports from monitoring data."""

    def __init__(self):
        """Initialize report generator."""
        # Set up Jinja2 environment
        template_dir = Path(__file__).parent / "templates"
        self.jinja_env = Environment(loader=FileSystemLoader(str(template_dir)))

    def generate_report(
        self,
        test_config: dict,
        test_results: dict,
        performance_samples: List[MetricSample],
        stability_report: Optional[StabilityReport],
        output_path: str,
        performance_monitor=None,
    ) -> None:
        """Generate a test report.

        Args:
            test_config: Test configuration dictionary
            test_results: Test execution results
            performance_samples: List of performance metric samples
            stability_report: Stability monitoring report
            output_path: Output file path
            performance_monitor: Performance monitor instance (for battery data)
        """
        output_file = Path(output_path)

        # Analyze performance
        performance_stats = self._analyze_performance(performance_samples)

        # Generate charts
        charts = {}
        if performance_samples:
            stability_issues = stability_report.issues if stability_report else None
            charts = ChartGenerator.generate_performance_charts(
                performance_samples,
                stability_issues
            )

        # Extract battery data from monitor if available
        battery_drain_mah = None
        battery_components = {}
        if performance_monitor and hasattr(performance_monitor, 'battery_drain_mah'):
            battery_drain_mah = performance_monitor.battery_drain_mah
            battery_components = performance_monitor.battery_components

        # Prepare template data
        template_data = self._prepare_template_data(
            test_config,
            test_results,
            performance_stats,
            charts,
            stability_report,
            battery_drain_mah,
            battery_components,
        )

        # Generate HTML report
        if output_file.suffix in ['.html', '.htm'] or output_file.suffix == '':
            html_path = output_file.with_suffix('.html')
            self._generate_html_report(template_data, html_path)
            print(f"HTML report generated: {html_path}")

        # Also generate JSON report
        json_path = output_file.with_suffix('.json')
        self._generate_json_report(
            test_config,
            test_results,
            performance_stats,
            stability_report,
            json_path,
        )
        print(f"JSON report generated: {json_path}")

    def _prepare_template_data(
        self,
        test_config: dict,
        test_results: dict,
        performance_stats: dict,
        charts: dict,
        stability_report: Optional[StabilityReport],
        battery_drain_mah: Optional[float] = None,
        battery_components: dict = None,
    ) -> dict:
        """Prepare data for HTML template.

        Args:
            test_config: Test configuration
            test_results: Test results
            performance_stats: Performance statistics
            charts: Generated charts
            stability_report: Stability report

        Returns:
            Dictionary with template data
        """
        # Calculate success rate
        total_events = test_results.get('events_executed', 0) + test_results.get('events_failed', 0)
        success_rate = (test_results.get('events_executed', 0) / total_events * 100) if total_events > 0 else 0

        # Prepare stability issues
        stability_issues = []
        total_crashes = 0
        total_anrs = 0
        total_errors = 0

        if stability_report:
            total_crashes = stability_report.total_crashes
            total_anrs = stability_report.total_anrs
            total_errors = stability_report.total_errors
            stability_issues = [
                {
                    'type': issue.type.value,
                    'timestamp': issue.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    'message': issue.message,
                    'stacktrace': issue.stacktrace,
                    'severity': issue.severity,
                }
                for issue in stability_report.issues
            ]

        return {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'device_name': test_config.get('device_id', 'Unknown'),
            'platform': test_config.get('platform', 'Unknown'),
            'device_id': test_config.get('device_id', 'Unknown'),
            'app_package': test_config.get('app_package', 'Unknown'),
            'tap_ratio': test_config.get('event_ratios', {}).get('tap', 0) * 100,
            'swipe_ratio': test_config.get('event_ratios', {}).get('swipe', 0) * 100,
            'interval_ms': test_config.get('interval_ms', 0),
            'event_count': test_config.get('event_count'),
            'duration_seconds': test_config.get('duration_seconds'),
            'events_executed': test_results.get('events_executed', 0),
            'events_failed': test_results.get('events_failed', 0),
            'duration': round(test_results.get('duration_seconds', 0), 1),
            'success_rate': round(success_rate, 1),
            'total_crashes': total_crashes,
            'total_anrs': total_anrs,
            'total_errors': total_errors,
            'performance_stats': performance_stats,
            'charts': charts,
            'stability_issues': stability_issues,
            'battery_drain_mah': battery_drain_mah,
            'battery_components': battery_components or {},
        }

    def _generate_html_report(self, template_data: dict, output_path: Path) -> None:
        """Generate HTML report.

        Args:
            template_data: Data for template
            output_path: Output file path
        """
        template = self.jinja_env.get_template('report.html')
        html_content = template.render(**template_data)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

    def _generate_json_report(
        self,
        test_config: dict,
        test_results: dict,
        performance_stats: dict,
        stability_report: Optional[StabilityReport],
        output_path: Path,
    ) -> None:
        """Generate JSON report.

        Args:
            test_config: Test configuration
            test_results: Test results
            performance_stats: Performance statistics
            stability_report: Stability report
            output_path: Output file path
        """
        # Convert datetime objects to ISO format strings
        serializable_results = dict(test_results)
        if "start_time" in serializable_results and serializable_results["start_time"]:
            serializable_results["start_time"] = serializable_results["start_time"].isoformat()
        if "end_time" in serializable_results and serializable_results["end_time"]:
            serializable_results["end_time"] = serializable_results["end_time"].isoformat()

        report_data = {
            "generated_at": datetime.now().isoformat(),
            "config": test_config,
            "results": serializable_results,
            "performance": performance_stats,
            "stability": stability_report.to_dict() if stability_report else None,
        }

        with open(output_path, 'w') as f:
            json.dump(report_data, f, indent=2)

    def _analyze_performance(self, samples: List[MetricSample]) -> dict:
        """Analyze performance metrics.

        Args:
            samples: List of metric samples

        Returns:
            Dictionary with performance statistics
        """
        if not samples:
            return {}

        # Extract metric values
        cpu_values = [s.cpu_percent for s in samples if s.cpu_percent is not None]
        memory_values = [s.memory_mb for s in samples if s.memory_mb is not None]
        fps_values = [s.fps for s in samples if s.fps is not None]
        temperature_values = [s.temperature for s in samples if s.temperature is not None]

        return {
            "cpu": Statistics.compute_all_stats(cpu_values) if cpu_values else None,
            "memory": Statistics.compute_all_stats(memory_values) if memory_values else None,
            "fps": Statistics.compute_all_stats(fps_values) if fps_values else None,
            "temperature": Statistics.compute_all_stats(temperature_values) if temperature_values else None,
            "sample_count": len(samples),
        }
