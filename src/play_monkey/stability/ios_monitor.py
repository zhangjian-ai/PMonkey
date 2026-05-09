"""iOS stability monitoring using pymobiledevice3 crash reports and oslog."""

import json
import re
import subprocess
import threading
from collections import deque
from datetime import datetime
from typing import Deque, List, Optional, Tuple

from .base import StabilityMonitor
from .models import StabilityIssue, StabilityIssueType, StabilityReport, StabilityStatus


# oslog line structure:
# [timestamp][subsystem][category][pid][process] <Level>: message
# Example (system, NOT our app):
#   [2026-05-08 16:22:54.039274][com.apple.WiFiPolicy][][53][WiFiPolicy] <Default>: ... fgApp: com.example ...
# Example (our app):
#   [2026-05-08 ...][com.merge.foodie.cooking.restaurant][net][1234][MergeFoodie] <Error>: NSURLSession failed
_OSLOG_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]*)\]"
    r"\[(?P<subsystem>[^\]]*)\]"
    r"\[(?P<category>[^\]]*)\]"
    r"\[(?P<pid>[^\]]*)\]"
    r"\[(?P<process>[^\]]*)\]"
    r"\s*<(?P<level>[^>]+)>:\s*(?P<message>.*)$"
)


class IOSStabilityMonitor(StabilityMonitor):
    """iOS stability monitor.

    Uses two background threads:
    - crash watch: ``pymobiledevice3 crash watch`` for real-time crash detection
    - oslog: ``pymobiledevice3 developer dvt oslog`` for error log collection
    """

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.app_package: Optional[str] = None
        self.issues: List[StabilityIssue] = []
        self._monitoring = False

        self._crash_thread: Optional[threading.Thread] = None
        self._oslog_thread: Optional[threading.Thread] = None
        self._crash_process: Optional[subprocess.Popen] = None
        self._oslog_process: Optional[subprocess.Popen] = None

        self._lock = threading.Lock()
        self._recent_messages: Deque[str] = deque(maxlen=200)
        self._has_unhandled_crash = False

    # ── StabilityMonitor interface ──────────────────────────────────

    def start_monitoring(self, app_identifier: str) -> None:
        self.app_package = app_identifier
        self.issues = []
        self._recent_messages.clear()
        self._has_unhandled_crash = False
        self._monitoring = True

        # Flush pending crash reports so we only see new ones
        self._run_pmd3("crash flush", timeout=10)

        self._crash_thread = threading.Thread(target=self._consume_crash_watch, daemon=True)
        self._crash_thread.start()

        self._oslog_thread = threading.Thread(target=self._consume_oslog, daemon=True)
        self._oslog_thread.start()

    def check_stability(self) -> StabilityStatus:
        """Pure in-memory read: no subprocess calls. App liveness is not
        queried here - crashes are detected by the background crash watch
        thread.
        """
        with self._lock:
            error_count = sum(1 for i in self.issues if i.type == StabilityIssueType.ERROR)
            has_crashed = self._has_unhandled_crash
            self._has_unhandled_crash = False

        return StabilityStatus(
            is_running=True,
            has_crashed=has_crashed,
            has_anr=False,
            error_count=error_count,
        )

    def get_issues(self) -> List[StabilityIssue]:
        with self._lock:
            return list(self.issues)

    def stop_monitoring(self) -> StabilityReport:
        self._monitoring = False

        for proc in (self._crash_process, self._oslog_process):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()

        for thread in (self._crash_thread, self._oslog_thread):
            if thread and thread.is_alive():
                thread.join(timeout=3)

        with self._lock:
            crashes = sum(1 for i in self.issues if i.type == StabilityIssueType.CRASH)
            errors = sum(1 for i in self.issues if i.type == StabilityIssueType.ERROR)
            issues = list(self.issues)

        return StabilityReport(
            total_crashes=crashes,
            total_anrs=0,
            total_errors=errors,
            issues=issues,
        )

    # ── helpers ─────────────────────────────────────────────────────

    def _run_pmd3(self, command: str, timeout: int = 5) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                f"pymobiledevice3 {command}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode == 0, result.stdout
        except Exception:
            return False, ""

    # ── crash watch thread ──────────────────────────────────────────

    def _consume_crash_watch(self) -> None:
        """Watch for new crash reports via ``pymobiledevice3 crash watch``."""
        command = ["pymobiledevice3", "crash", "watch", "--udid", self.device_id]
        if self.app_package:
            command.extend(["--name", self.app_package])

        try:
            self._crash_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception:
            return

        assert self._crash_process.stdout is not None
        crash_buffer: list[str] = []
        in_report = False

        for raw_line in self._crash_process.stdout:
            if not self._monitoring:
                break
            line = raw_line.rstrip("\n")

            # crash watch outputs structured crash report blocks
            if "Incident Identifier" in line or "Exception Type" in line:
                in_report = True

            if in_report:
                crash_buffer.append(line)

            # A blank line after content signals end of a report section
            if in_report and line.strip() == "" and len(crash_buffer) > 3:
                self._handle_crash_report(crash_buffer)
                crash_buffer = []
                in_report = False

        # Flush remaining buffer
        if crash_buffer:
            self._handle_crash_report(crash_buffer)

    def _handle_crash_report(self, lines: list[str]) -> None:
        """Parse a crash report block and record the issue."""
        full_text = "\n".join(lines)

        # Extract key fields
        exception_type = ""
        exception_reason = ""
        process_name = ""

        for line in lines:
            if line.startswith("Exception Type:"):
                exception_type = line.split(":", 1)[1].strip()
            elif line.startswith("Exception Codes:") or line.startswith("Exception Subtype:"):
                exception_reason = line.split(":", 1)[1].strip()
            elif line.startswith("Process:"):
                process_name = line.split(":", 1)[1].strip()

        # Skip if not related to our app
        if self.app_package and self.app_package not in full_text and self.app_package not in process_name:
            return

        message = exception_type or "Crash detected"
        if exception_reason:
            message = f"{message} - {exception_reason}"
        if process_name:
            message = f"[{process_name}] {message}"

        self._record_issue(StabilityIssueType.CRASH, message, "critical", stacktrace=full_text)
        with self._lock:
            self._has_unhandled_crash = True

    # ── oslog thread ────────────────────────────────────────────────

    def _consume_oslog(self) -> None:
        """Consume oslog for error-level log collection."""
        if not self.app_package:
            return

        command = ["pymobiledevice3", "developer", "dvt", "oslog", "--udid", self.device_id]
        try:
            self._oslog_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception:
            return

        assert self._oslog_process.stdout is not None
        for raw_line in self._oslog_process.stdout:
            if not self._monitoring:
                break
            line = raw_line.strip()
            if not line:
                continue
            self._process_oslog_line(line)

    def _process_oslog_line(self, line: str) -> None:
        """Filter oslog lines to only the ones our app actually emitted.

        iOS system components (WiFi, networking, etc.) often include the
        foreground app's bundle id in their log messages (e.g.
        ``fgApp: com.example.app``). A naive ``bundle_id in line`` check
        picks those up as "app errors" and floods the report with
        unrelated system noise.

        Instead we parse the structured oslog fields and only keep lines
        where subsystem or process identifies our app, and whose level
        is actually Error/Fault (not just the word 'error' appearing
        somewhere inside the message like ``TxFwFail: 0``).
        """
        if not self.app_package:
            return

        m = _OSLOG_LINE_RE.match(line)
        if not m:
            return

        subsystem = m.group("subsystem")
        process = m.group("process")
        level = m.group("level").strip().lower()
        message = m.group("message")

        # Only take logs whose source IS our app. subsystem usually matches
        # the bundle id exactly; process is the executable name, which for
        # apps like "com.merge.foodie.cooking.restaurant" is "MergeFoodie" -
        # fall back to a keyword match from the bundle parts for that case.
        if subsystem == self.app_package:
            source_matches = True
        else:
            keywords = [p for p in self.app_package.lower().split(".") if len(p) > 3]
            proc_lower = process.lower()
            source_matches = any(k in proc_lower for k in keywords)

        if not source_matches:
            return

        # Only real error/fault log levels, not the substring "error" in
        # benign counter fields.
        if level not in ("error", "fault"):
            return

        severity = "high" if level == "fault" else "medium"
        # Keep the message concise; the raw line is preserved as context.
        display_message = f"<{level}> {message}" if message else line
        self._record_issue(StabilityIssueType.ERROR, display_message, severity)

    # ── shared recording ────────────────────────────────────────────

    def _record_issue(
        self,
        issue_type: StabilityIssueType,
        message: str,
        severity: str,
        stacktrace: Optional[str] = None,
    ) -> None:
        with self._lock:
            if message in self._recent_messages:
                return
            self._recent_messages.append(message)
            self.issues.append(
                StabilityIssue(
                    type=issue_type,
                    timestamp=datetime.now(),
                    message=message,
                    stacktrace=stacktrace,
                    severity=severity,
                )
            )

    def _is_app_running(self) -> bool:
        if not self.app_package:
            return False
        success, output = self._run_pmd3(
            f"developer dvt process-id-for-bundle-id {self.app_package} --udid {self.device_id}"
        )
        return success and output.strip().isdigit()
