"""Android stability monitoring using ADB, logcat, and dropbox.

Layered detection strategy:
1. Real-time detection: logcat -b crash stream for immediate crash/ANR flags
2. Full stack traces: periodic dumpsys dropbox --print polling for complete traces
3. Non-fatal errors: logcat *:E --pid filtering with multi-line stack grouping
4. Final scan: dropbox query after test ends to ensure nothing is missed
"""

import re
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from typing import Deque, List, Optional, Set

from .base import StabilityMonitor
from .models import StabilityIssue, StabilityIssueType, StabilityReport, StabilityStatus


class AndroidStabilityMonitor(StabilityMonitor):
    """Android stability monitor with dropbox-backed full stack traces."""

    def __init__(self, device_id: str, adb_path: str = "adb"):
        self.device_id = device_id
        self.adb_path = adb_path
        self.app_package: Optional[str] = None
        self.app_pid: Optional[int] = None
        self.issues: List[StabilityIssue] = []
        self._monitoring = False
        self._start_time: Optional[datetime] = None
        
        # Real-time detection threads
        self._crash_logcat_thread: Optional[threading.Thread] = None
        self._error_logcat_thread: Optional[threading.Thread] = None
        self._dropbox_thread: Optional[threading.Thread] = None
        self._flush_thread: Optional[threading.Thread] = None
        
        # Processes
        self._crash_logcat_proc: Optional[subprocess.Popen] = None
        self._error_logcat_proc: Optional[subprocess.Popen] = None
        
        # Thread-safe state
        self._lock = threading.Lock()
        self._recent_issue_keys: Set[str] = set()
        self._has_unhandled_crash = False
        self._has_unhandled_anr = False
        
        # Error log buffering (for multi-line stack grouping)
        self._error_buffer: List[str] = []
        self._last_error_time: Optional[float] = None

    def start_monitoring(self, app_identifier: str) -> None:
        """Start monitoring the specified app."""
        self.app_package = app_identifier
        self.app_pid = self._get_app_pid()
        self.issues = []
        self._recent_issue_keys.clear()
        self._has_unhandled_crash = False
        self._has_unhandled_anr = False
        self._monitoring = True
        self._start_time = datetime.now()
        self._error_buffer = []
        self._last_error_time = None

        # Clear logcat
        self._run_adb_command("logcat -c", timeout=5)

        # Start real-time crash detection (logcat -b crash)
        self._crash_logcat_thread = threading.Thread(
            target=self._consume_crash_logcat,
            name="android-crash-logcat",
            daemon=True
        )
        self._crash_logcat_thread.start()

        # Start error log monitoring (logcat *:E --pid)
        if self.app_pid:
            self._error_logcat_thread = threading.Thread(
                target=self._consume_error_logcat,
                name="android-error-logcat",
                daemon=True
            )
            self._error_logcat_thread.start()

        # Start background dropbox polling (every 5 seconds)
        self._dropbox_thread = threading.Thread(
            target=self._poll_dropbox,
            name="android-dropbox-poller",
            daemon=True
        )
        self._dropbox_thread.start()

        # Start background flush loop (checks every 500ms for stale buffers)
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            name="android-error-flusher",
            daemon=True
        )
        self._flush_thread.start()

    def check_stability(self) -> StabilityStatus:
        """Check current stability status (pure memory read)."""
        with self._lock:
            error_count = sum(1 for issue in self.issues if issue.type == StabilityIssueType.ERROR)
            has_crashed = self._has_unhandled_crash
            has_anr = self._has_unhandled_anr
            self._has_unhandled_crash = False
            self._has_unhandled_anr = False

        return StabilityStatus(
            is_running=True,
            has_crashed=has_crashed,
            has_anr=has_anr,
            error_count=error_count,
        )

    def get_issues(self) -> List[StabilityIssue]:
        """Get all detected stability issues."""
        with self._lock:
            return list(self.issues)

    def stop_monitoring(self) -> StabilityReport:
        """Stop monitoring and return complete report."""
        self._monitoring = False

        # Flush any buffered error logs
        self._flush_error_buffer()

        # Stop all threads
        if self._crash_logcat_proc and self._crash_logcat_proc.poll() is None:
            self._crash_logcat_proc.terminate()
            try:
                self._crash_logcat_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._crash_logcat_proc.kill()

        if self._error_logcat_proc and self._error_logcat_proc.poll() is None:
            self._error_logcat_proc.terminate()
            try:
                self._error_logcat_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._error_logcat_proc.kill()

        if self._crash_logcat_thread and self._crash_logcat_thread.is_alive():
            self._crash_logcat_thread.join(timeout=2)
        if self._error_logcat_thread and self._error_logcat_thread.is_alive():
            self._error_logcat_thread.join(timeout=2)
        if self._dropbox_thread and self._dropbox_thread.is_alive():
            self._dropbox_thread.join(timeout=2)
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=2)

        # Final dropbox scan to catch anything missed
        self._scan_dropbox_final()

        with self._lock:
            crashes = sum(1 for issue in self.issues if issue.type == StabilityIssueType.CRASH)
            anrs = sum(1 for issue in self.issues if issue.type == StabilityIssueType.ANR)
            errors = sum(1 for issue in self.issues if issue.type == StabilityIssueType.ERROR)
            issues = list(self.issues)

        return StabilityReport(
            total_crashes=crashes,
            total_anrs=anrs,
            total_errors=errors,
            issues=issues,
        )

    def _run_adb_command(self, command: str, timeout: int = 5) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                f"{self.adb_path} -s {self.device_id} {command}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode == 0, result.stdout
        except Exception:
            return False, ""

    def _get_app_pid(self) -> Optional[int]:
        """Get the PID of the target app."""
        if not self.app_package:
            return None
        success, output = self._run_adb_command(f"shell pidof {self.app_package}")
        if success and output.strip():
            try:
                return int(output.strip().split()[0])
            except (ValueError, IndexError):
                pass
        return None

    def _consume_crash_logcat(self) -> None:
        """Background thread: monitor logcat -b crash for immediate crash/ANR detection."""
        if not self.app_package:
            return

        command = [self.adb_path, "-s", self.device_id, "logcat", "-b", "crash", "-v", "brief"]
        try:
            self._crash_logcat_proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception:
            return

        assert self._crash_logcat_proc.stdout is not None
        for raw_line in self._crash_logcat_proc.stdout:
            if not self._monitoring:
                break
            line = raw_line.strip()
            if not line or self.app_package not in line:
                continue

            line_lower = line.lower()
            if "fatal exception" in line_lower or "androidruntime" in line_lower:
                with self._lock:
                    self._has_unhandled_crash = True
            elif "anr" in line_lower:
                with self._lock:
                    self._has_unhandled_anr = True

    def _consume_error_logcat(self) -> None:
        """Background thread: monitor logcat *:E --pid for non-fatal errors."""
        if not self.app_pid:
            return

        command = [
            self.adb_path, "-s", self.device_id, "logcat",
            "-v", "brief", "*:E", "--pid", str(self.app_pid)
        ]
        try:
            self._error_logcat_proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception:
            return

        assert self._error_logcat_proc.stdout is not None
        for raw_line in self._error_logcat_proc.stdout:
            if not self._monitoring:
                break
            line = raw_line.strip()
            if not line:
                continue
            self._process_error_line(line)

        # Flush any remaining buffered errors
        self._flush_error_buffer()

    def _process_error_line(self, line: str) -> None:
        """Process an error log line, grouping multi-line stack traces.

        Strategy: Use a time-based grouping. Lines arriving within 500ms of
        each other are considered part of the same error event. The first
        flush happens when 500ms passes with no new lines.
        """
        # Filter out logcat noise
        if line.startswith("---------") or line.startswith("=========="):
            return
        if "beginning of" in line.lower():
            return
        if not line.strip():
            return

        # Extract message after logcat prefix
        msg_match = re.match(r'^[VDIWEF]/\S+\s+\(\s*\d+\):\s*(.*)$', line)
        actual_line = msg_match.group(1) if msg_match else line

        # Detect a new exception header that should start a new error group
        is_exception_header = bool(re.search(
            r'\b(Exception|Error|FATAL EXCEPTION|Traceback)\b',
            actual_line
        )) and not actual_line.strip().startswith("at ")

        with self._lock:
            # If buffer is empty, just buffer this line
            if not self._error_buffer:
                self._error_buffer.append(line)
                self._last_error_time = time.time()
                return

            # If this is a new exception header and buffer already has an exception,
            # flush the previous group and start a new one
            if is_exception_header:
                self._flush_error_buffer_locked()
                self._error_buffer.append(line)
                self._last_error_time = time.time()
                return

            # Otherwise, append to current buffer (it's likely a stack frame)
            self._error_buffer.append(line)
            self._last_error_time = time.time()

            # Hard cap on buffer size
            if len(self._error_buffer) > 200:
                self._flush_error_buffer_locked()

    def _flush_error_buffer(self) -> None:
        """Public flush, takes the lock."""
        with self._lock:
            self._flush_error_buffer_locked()

    def _flush_error_buffer_locked(self) -> None:
        """Flush buffered error lines as a single error issue. Caller holds lock."""
        if not self._error_buffer:
            return

        # Filter noise
        meaningful_lines = [
            line for line in self._error_buffer
            if line.strip() and not line.startswith("---------")
        ]

        if not meaningful_lines:
            self._error_buffer = []
            self._last_error_time = None
            return

        # First line is the error message, full buffer is the stack trace
        message = meaningful_lines[0]
        # Trim logcat prefix from message for readability
        msg_match = re.match(r'^[VDIWEF]/\S+\s+\(\s*\d+\):\s*(.*)$', message)
        if msg_match:
            message = msg_match.group(1)

        stacktrace = "\n".join(meaningful_lines)

        # Inline record (we already hold the lock)
        key = f"{StabilityIssueType.ERROR.value}:{message[:100]}"
        if key not in self._recent_issue_keys:
            self._recent_issue_keys.add(key)
            if len(self._recent_issue_keys) > 500:
                self._recent_issue_keys.clear()
            self.issues.append(
                StabilityIssue(
                    type=StabilityIssueType.ERROR,
                    timestamp=datetime.now(),
                    message=message,
                    stacktrace=stacktrace,
                    severity="medium",
                )
            )

        self._error_buffer = []
        self._last_error_time = None

    def _flush_loop(self) -> None:
        """Background thread: periodically flush stale error buffers.

        Without this, a buffered error sits forever if no new logs arrive.
        """
        while self._monitoring:
            time.sleep(0.5)
            if not self._monitoring:
                break
            with self._lock:
                if self._error_buffer and self._last_error_time:
                    if time.time() - self._last_error_time > 0.5:
                        self._flush_error_buffer_locked()

    def _flush_error_buffer(self) -> None:
        """Flush buffered error lines as a single error issue."""
        if not self._error_buffer:
            return

        # Filter out empty or noise-only buffers
        meaningful_lines = [
            line for line in self._error_buffer
            if line.strip() and not line.startswith("---------")
        ]

        if not meaningful_lines:
            self._error_buffer = []
            self._last_error_time = None
            return

        # First line is the error message, rest is stack trace
        message = meaningful_lines[0]
        stacktrace = "\n".join(meaningful_lines) if len(meaningful_lines) > 1 else None

        self._record_issue(
            issue_type=StabilityIssueType.ERROR,
            message=message,
            stacktrace=stacktrace,
            severity="medium"
        )

        self._error_buffer = []
        self._last_error_time = None

    def _poll_dropbox(self) -> None:
        """Background thread: periodically poll dropbox for full crash/ANR traces."""
        while self._monitoring:
            time.sleep(5)  # Poll every 5 seconds
            if not self._monitoring:
                break
            self._scan_dropbox()

    def _scan_dropbox(self) -> None:
        """Scan dropbox for crash/ANR entries since monitoring started."""
        if not self.app_package or not self._start_time:
            return

        # Query dropbox for crash entries
        for tag in ["data_app_crash", "data_app_native_crash", "data_app_anr"]:
            success, output = self._run_adb_command(
                f"shell dumpsys dropbox --print {tag}",
                timeout=10
            )
            if not success or not output:
                continue

            self._parse_dropbox_entries(output, tag)

    def _scan_dropbox_final(self) -> None:
        """Final dropbox scan after monitoring stops."""
        self._scan_dropbox()

    def _parse_dropbox_entries(self, output: str, tag: str) -> None:
        """Parse dropbox entries and extract full stack traces."""
        if not self.app_package:
            return

        # Split by entry headers (e.g., "Drop box entry data_app_crash at ...")
        entries = re.split(r"Drop box entry \w+ at ", output)
        
        for entry in entries:
            if not entry.strip():
                continue
            
            # Check if this entry is for our app
            if self.app_package not in entry:
                continue
            
            # Extract timestamp from first line (format: "2026-05-07 10:30:45")
            timestamp_match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", entry)
            if timestamp_match:
                try:
                    entry_time = datetime.strptime(timestamp_match.group(1), "%Y-%m-%d %H:%M:%S")
                    # Only process entries after monitoring started
                    if self._start_time and entry_time < self._start_time:
                        continue
                except ValueError:
                    pass
            
            # Determine issue type
            if "anr" in tag.lower():
                issue_type = StabilityIssueType.ANR
                severity = "high"
            else:
                issue_type = StabilityIssueType.CRASH
                severity = "critical"
            
            # Extract exception/error message (first meaningful line)
            lines = entry.splitlines()
            message = "Unknown crash"
            for line in lines[:20]:  # Check first 20 lines
                line = line.strip()
                if "Exception" in line or "Error" in line or "FATAL" in line:
                    message = line
                    break
            
            # Full entry is the stack trace
            stacktrace = entry.strip()
            
            self._record_issue(
                issue_type=issue_type,
                message=message,
                stacktrace=stacktrace,
                severity=severity
            )

    def _record_issue(
        self,
        issue_type: StabilityIssueType,
        message: str,
        stacktrace: Optional[str] = None,
        severity: str = "medium"
    ) -> None:
        """Record a stability issue with deduplication."""
        # Create a unique key for deduplication
        key = f"{issue_type.value}:{message[:100]}"
        
        with self._lock:
            if key in self._recent_issue_keys:
                return
            
            self._recent_issue_keys.add(key)
            
            # Limit dedup set size
            if len(self._recent_issue_keys) > 500:
                self._recent_issue_keys.clear()
            
            self.issues.append(
                StabilityIssue(
                    type=issue_type,
                    timestamp=datetime.now(),
                    message=message,
                    stacktrace=stacktrace,
                    severity=severity,
                )
            )
