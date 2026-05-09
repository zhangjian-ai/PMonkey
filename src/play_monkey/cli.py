"""Command-line interface for play-monkey."""

import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from .config.models import TestConfig
from .config.validator import ConfigValidator

console = Console()


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """Play-Monkey: Cross-platform mobile monkey testing tool."""
    pass


@main.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Path to config file")
@click.option("--platform", type=click.Choice(["android", "ios"]), help="Target platform")
@click.option("--device", help="Device ID")
@click.option("--app", help="App package name or bundle ID")
@click.option("--tap-ratio", type=float, help="Tap event ratio (0.0-1.0)")
@click.option("--swipe-ratio", type=float, help="Swipe event ratio (0.0-1.0)")
@click.option("--events", type=int, help="Total number of events")
@click.option("--duration", type=int, help="Test duration in seconds")
@click.option("--interval", type=int, default=500, help="Interval between events (ms)")
@click.option("--output", "-o", default="./report.html", help="Report output path")
def run(
    config: Optional[str],
    platform: Optional[str],
    device: Optional[str],
    app: Optional[str],
    tap_ratio: Optional[float],
    swipe_ratio: Optional[float],
    events: Optional[int],
    duration: Optional[int],
    interval: int,
    output: str,
) -> None:
    """Run monkey testing."""
    try:
        # Load configuration
        if config:
            test_config = ConfigValidator.load_from_file(config)
            console.print(f"[green]✓[/green] Loaded config from {config}")
        else:
            # Build config from command-line arguments
            if not all([platform, device, app]):
                console.print("[red]Error:[/red] Must provide --platform, --device, and --app")
                sys.exit(1)

            if tap_ratio is None or swipe_ratio is None:
                console.print("[red]Error:[/red] Must provide --tap-ratio and --swipe-ratio")
                sys.exit(1)

            if events is None and duration is None:
                console.print("[red]Error:[/red] Must provide either --events or --duration")
                sys.exit(1)

            # TODO: Build TestConfig from CLI args
            console.print("[yellow]Warning:[/yellow] CLI-only mode not yet implemented")
            sys.exit(1)

        console.print(f"[cyan]Platform:[/cyan] {test_config.platform.value}")
        console.print(f"[cyan]Device:[/cyan] {test_config.device_id}")
        console.print(f"[cyan]App:[/cyan] {test_config.app_package}")

        # Import required modules
        from .config.models import Platform
        from .core.events import EventGenerator
        from .core.scheduler import MonkeyScheduler
        from .devices.factory import DeviceFactory
        from .monitoring.android_monitor import AndroidMonitor
        from .monitoring.ios_monitor import IOSMonitor
        from .reporting.generator import ReportGenerator
        from .stability.android_monitor import AndroidStabilityMonitor
        from .stability.ios_monitor import IOSStabilityMonitor
        from .stability.recovery import RecoveryStrategy

        # Create device
        console.print("\n[cyan]Connecting to device...[/cyan]")
        device = DeviceFactory.create_device(
            test_config.device_id,
            test_config.platform
        )

        if not device.connect():
            console.print("[red]Failed to connect to device[/red]")
            sys.exit(1)

        console.print("[green]✓[/green] Connected to device")

        # Get screen size
        screen_width, screen_height = device.get_screen_size()
        console.print(f"[cyan]Screen size:[/cyan] {screen_width}x{screen_height}")

        # Auto-initialize bounds from screen size if not configured
        from .config.models import BoundsConfig
        bounds = test_config.bounds
        if bounds is None:
            bounds = BoundsConfig(
                x_min=0,
                x_max=screen_width - 1,
                y_min=0,
                y_max=screen_height - 1
            )
            console.print(f"[cyan]Bounds:[/cyan] Full screen (0, 0) -> ({screen_width - 1}, {screen_height - 1})")
        else:
            console.print(f"[cyan]Bounds:[/cyan] ({bounds.x_min}, {bounds.y_min}) -> ({bounds.x_max}, {bounds.y_max})")

        # Show exclusion zones if configured
        if test_config.exclusion_zones:
            console.print(f"[cyan]Exclusion zones:[/cyan] {len(test_config.exclusion_zones)} zone(s)")
            for i, zone in enumerate(test_config.exclusion_zones, 1):
                console.print(f"  Zone {i}: ({zone.x_min}, {zone.y_min}) -> ({zone.x_max}, {zone.y_max})")

        # Create event generator
        event_generator = EventGenerator(
            test_config.event_ratios.tap,
            test_config.event_ratios.swipe,
            bounds,
            screen_width,
            screen_height,
            swipe_duration_min_ms=test_config.swipe_duration.min_ms,
            swipe_duration_max_ms=test_config.swipe_duration.max_ms,
            exclusion_zones=test_config.exclusion_zones,
        )

        # Create monitors based on platform
        performance_monitor = None
        stability_monitor = None
        recovery_strategy = None
        is_android = test_config.platform == Platform.ANDROID

        if test_config.monitoring.enabled:
            console.print("[cyan]Starting performance monitoring...[/cyan]")
            if is_android:
                performance_monitor = AndroidMonitor(
                    test_config.device_id,
                    sample_interval_seconds=test_config.monitoring.sample_interval_seconds,
                )
            else:
                performance_monitor = IOSMonitor(
                    test_config.device_id,
                    sample_interval_seconds=test_config.monitoring.sample_interval_seconds,
                )

        if test_config.stability.monitor_crashes or test_config.stability.monitor_anr:
            console.print("[cyan]Starting stability monitoring...[/cyan]")
            if is_android:
                stability_monitor = AndroidStabilityMonitor(test_config.device_id)
            else:
                stability_monitor = IOSStabilityMonitor(test_config.device_id)

        # Create recovery strategy if crash/ANR recovery is enabled
        if test_config.stability.continue_on_crash or test_config.stability.continue_on_anr:
            console.print("[cyan]Enabling crash/ANR recovery...[/cyan]")
            recovery_strategy = RecoveryStrategy(device, test_config.app_package)

        # Create scheduler
        scheduler = MonkeyScheduler(test_config, event_generator)

        # Run test
        console.print("\n[green]Starting monkey test...[/green]")
        results = scheduler.run(device, performance_monitor, stability_monitor, recovery_strategy)

        # Generate report
        console.print("\n[cyan]Generating report...[/cyan]")
        report_generator = ReportGenerator()

        performance_samples = performance_monitor.stop_monitoring() if performance_monitor else []
        stability_report = stability_monitor.stop_monitoring() if stability_monitor else None

        report_generator.generate_report(
            test_config.model_dump(),
            results,
            performance_samples,
            stability_report,
            test_config.report.output_path,
            performance_monitor,
        )

        # Print summary
        console.print("\n[green]✓ Test completed![/green]")
        console.print(f"Events executed: {results['events_executed']}")
        console.print(f"Events failed: {results['events_failed']}")
        console.print(f"Duration: {results['duration_seconds']:.1f}s")

        if stability_report:
            console.print(f"Crashes: {stability_report.total_crashes}")
            console.print(f"ANRs: {stability_report.total_anrs}")

        device.disconnect()

    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        sys.exit(1)


@main.command()
def list_devices() -> None:
    """List available devices."""
    from .devices.factory import DeviceFactory, DeviceInfo

    console.print("[cyan]Scanning for devices...[/cyan]\n")

    def _format(d: DeviceInfo) -> str:
        # device_id is the only required column; everything else is shown
        # in parentheses if we managed to read it.
        extras = []
        if d.name:
            extras.append(d.name)
        if d.os_version:
            extras.append(f"iOS {d.os_version}" if d.platform.value == "ios"
                          else f"Android {d.os_version}")
        if d.resolution:
            extras.append(d.resolution)
        suffix = f"  ({', '.join(extras)})" if extras else ""
        return f"  • {d.device_id}{suffix}"

    # Detect Android devices
    android_devices = DeviceFactory.detect_android_devices()
    if android_devices:
        console.print("[green]Android Devices:[/green]")
        for device in android_devices:
            console.print(_format(device))
    else:
        console.print("[yellow]No Android devices found[/yellow]")

    # Detect iOS devices
    ios_devices = DeviceFactory.detect_ios_devices()
    if ios_devices:
        console.print("\n[green]iOS Devices:[/green]")
        for device in ios_devices:
            console.print(_format(device))
    else:
        console.print("\n[yellow]No iOS devices found[/yellow]")

    if not android_devices and not ios_devices:
        console.print("\n[yellow]No devices found. Make sure devices are connected and authorized.[/yellow]")


@main.command()
@click.argument("config_path", type=click.Path(exists=True))
def validate_config(config_path: str) -> None:
    """Validate a configuration file."""
    try:
        config = ConfigValidator.load_from_file(config_path)
        console.print(f"[green]✓[/green] Configuration is valid")

        # Display config summary
        table = Table(title="Configuration Summary")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Platform", config.platform.value)
        table.add_row("Device", config.device_id)
        table.add_row("App", config.app_package)
        table.add_row("Tap Ratio", f"{config.event_ratios.tap:.1%}")
        table.add_row("Swipe Ratio", f"{config.event_ratios.swipe:.1%}")

        if config.event_count:
            table.add_row("Event Count", str(config.event_count))
        if config.duration_seconds:
            table.add_row("Duration", f"{config.duration_seconds}s")

        console.print(table)

    except Exception as e:
        console.print(f"[red]✗[/red] Configuration is invalid: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
