"""Monkey test scheduler and orchestrator."""

import logging
import threading
import time
from datetime import datetime
from typing import Optional

from rich.live import Live
from rich.table import Table

from ..config.models import TestConfig
from .events import Event, EventGenerator

logger = logging.getLogger(__name__)


class _SamplerThread:
    """Background thread that drives performance sampling at a fixed cadence.

    Kept out of the main event loop so subprocess-heavy dumpsys calls never
    block event dispatch. The thread sleeps between samples and exits cleanly
    when the `stop` event is set.
    """

    def __init__(self, monitor, interval_seconds: float):
        self._monitor = monitor
        self._interval = max(0.1, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="play-monkey-sampler", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        # Wait one interval before the first sample so we don't hit the
        # device immediately after start_monitoring()
        while not self._stop.wait(self._interval):
            try:
                self._monitor.collect_sample()
            except Exception:
                # Never let a sampling failure kill the thread
                continue

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._interval + 2)
        self._thread = None


class MonkeyScheduler:
    """Main scheduler for monkey testing.

    Coordinates event generation, device execution, and monitoring.
    Performance sampling runs in a dedicated background thread and never
    blocks the event-dispatch loop.
    """

    def __init__(
        self,
        config: TestConfig,
        event_generator: EventGenerator,
    ):
        self.config = config
        self.event_generator = event_generator

        self.events_executed = 0
        self.events_failed = 0
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None

        self.crash_count = 0
        self.anr_count = 0

    def should_continue(self) -> bool:
        if self.config.stability.max_crash_count is not None:
            if self.crash_count >= self.config.stability.max_crash_count:
                return False

        # duration_seconds takes priority over event_count
        if self.config.duration_seconds is not None:
            if self.start_time is None:
                return True
            elapsed = (datetime.now() - self.start_time).total_seconds()
            if elapsed >= self.config.duration_seconds:
                return False

        if self.config.event_count is not None:
            return self.events_executed < self.config.event_count

        # duration_seconds set but not yet expired
        if self.config.duration_seconds is not None:
            return True

        return False

    def get_progress(self) -> dict:
        progress = {
            "events_executed": self.events_executed,
            "events_failed": self.events_failed,
            "crash_count": self.crash_count,
            "anr_count": self.anr_count,
        }

        if self.start_time:
            elapsed = (datetime.now() - self.start_time).total_seconds()
            progress["elapsed_seconds"] = elapsed

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

    def _build_progress_table(self) -> Table:
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("key", style="cyan", no_wrap=True)
        table.add_column("value", style="green")

        elapsed = 0.0
        if self.start_time:
            elapsed = (datetime.now() - self.start_time).total_seconds()

        if self.config.event_count is not None:
            pct = self.events_executed / self.config.event_count * 100
            table.add_row("Progress", f"{self.events_executed}/{self.config.event_count} ({pct:.0f}%)")
        else:
            table.add_row("Events", str(self.events_executed))

        table.add_row("Failed", str(self.events_failed))
        table.add_row("Elapsed", f"{elapsed:.1f}s")
        table.add_row("Crashes", str(self.crash_count))
        table.add_row("ANRs", str(self.anr_count))
        return table

    def run(self, device, performance_monitor=None, stability_monitor=None, recovery_strategy=None):
        """Run the monkey test.

        Event dispatch happens on the main thread; performance sampling and
        stability log readers run independently on daemon threads so they
        never block event throughput.
        """
        self.start_time = datetime.now()

        if performance_monitor:
            performance_monitor.start_monitoring(self.config.app_package)
        if stability_monitor:
            stability_monitor.start_monitoring(self.config.app_package)

        # Spin up the performance sampler on its own thread
        sampler: Optional[_SamplerThread] = None
        if performance_monitor:
            sampler = _SamplerThread(
                performance_monitor,
                self.config.monitoring.sample_interval_seconds,
            )
            sampler.start()

        interval_seconds = self.config.interval_ms / 1000.0
        stop_requested = False

        # Exponential backoff on consecutive failures. When WDA or the
        # app is in a bad state, hammering it with more requests only
        # makes things worse; giving it space to recover is the cheapest
        # way to get the run back on track without any special recovery
        # logic. One successful event resets the backoff.
        BACKOFF_INITIAL_S = 3.0
        BACKOFF_MAX_S = 30.0
        next_backoff = BACKOFF_INITIAL_S

        try:
            with Live(self._build_progress_table(), refresh_per_second=2) as live:
                while self.should_continue() and not stop_requested:
                    tick_start = time.monotonic()

                    # 1. Dispatch event (the only hot-path operation)
                    event = self.event_generator.generate()
                    success = self.execute_event(event, device)
                    event_elapsed_ms = (time.monotonic() - tick_start) * 1000
                    # Surface slow events so it's obvious when WDA is stalling
                    # on a particular tap (e.g. hitting a button that triggers
                    # a long-running app operation). This is the only way to
                    # tell "our code is stuck" apart from "WDA is working
                    # hard on a single action".
                    if event_elapsed_ms > 1000:
                        logger.warning(
                            "slow event #%d: %s took %.0fms (success=%s)",
                            self.events_executed + self.events_failed + 1,
                            event.__class__.__name__,
                            event_elapsed_ms,
                            success,
                        )
                    if success:
                        self.events_executed += 1
                        next_backoff = BACKOFF_INITIAL_S
                    else:
                        self.events_failed += 1

                    # 2. Stability check (pure in-memory read, non-blocking)
                    if stability_monitor:
                        status = stability_monitor.check_stability()

                        if status.has_crashed:
                            self.crash_count += 1
                            if self.config.stability.continue_on_crash and recovery_strategy:
                                recovered = recovery_strategy.handle_crash()
                                if not recovered:
                                    stop_requested = True
                            elif not self.config.stability.continue_on_crash:
                                stop_requested = True

                        if status.has_anr:
                            self.anr_count += 1
                            if self.config.stability.continue_on_anr and recovery_strategy:
                                recovered = recovery_strategy.handle_anr()
                                if not recovered:
                                    stop_requested = True
                            elif not self.config.stability.continue_on_anr:
                                stop_requested = True

                    live.update(self._build_progress_table())

                    # 3. Pacing.
                    #    - On failure: back off exponentially (3s, 6s, 12s,
                    #      24s, 30s, 30s...) to give WDA / the app time to
                    #      recover. A single success below resets the
                    #      backoff to 3s.
                    #    - On success: normal ticker pacing - keep event
                    #      starts interval_seconds apart, skipping the
                    #      sleep if the event itself already took longer.
                    if not success:
                        logger.warning(
                            "event failed; backing off %.0fs before next event",
                            next_backoff,
                        )
                        time.sleep(next_backoff)
                        next_backoff = min(next_backoff * 2, BACKOFF_MAX_S)
                    elif interval_seconds > 0:
                        remaining = interval_seconds - (time.monotonic() - tick_start)
                        if remaining > 0:
                            time.sleep(remaining)

        finally:
            # Stop sampler BEFORE calling monitor.stop_monitoring()
            # so we don't race with its final collect
            if sampler:
                sampler.stop()

            self.end_time = datetime.now()

            if performance_monitor:
                # One final sample, synchronously, so the report has a tail point
                try:
                    performance_monitor.collect_sample()
                except Exception:
                    pass
                performance_monitor.stop_monitoring()
            if stability_monitor:
                stability_monitor.stop_monitoring()

        return {
            "events_executed": self.events_executed,
            "events_failed": self.events_failed,
            "crash_count": self.crash_count,
            "anr_count": self.anr_count,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": (self.end_time - self.start_time).total_seconds(),
        }
