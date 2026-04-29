"""Android stability monitoring using ADB and logcat."""

import re
import subprocess
import threading
from datetime import datetime
from typing import List, Optional

from .base import StabilityMonitor
from .models import StabilityIssue, StabilityIssueType, StabilityReport, StabilityStatus


class AndroidStabilityMonitor(StabilityMonitor):
    """Android stability monitor using ADB and logcat."""

    def __init__(self, device_id: str, adb_path: str = "adb"):
        """Initialize Android stability monitor.

        Args:
            device_id: Device serial number
            adb_path: Path to ADB executable
        """
        self.device_id = device_id
        self.adb_path = adb_path
        self.app_package: Optional[str] = None
        self.issues: List[StabilityIssue] = []
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None

    def start_monitoring(self, app_identifier: str) -> None:
        """Start monitoring the specified app."""
        self.app_package = app_identifier
        self.issues = []
        self._monitoring = True

        # Start background monitoring thread
        # For now, we'll use a simplified approach without continuous logcat monitoring
        # TODO: Implement continuous logcat monitoring in background thread

    def check_stability(self) -> StabilityStatus:
        """Check current stability status."""
        # Check if app is still running
        is_running = self._is_app_running()

        # Check for recent crashes
        has_crashed = self._check_for_crash()

        # Check for ANR
        has_anr = self._check_for_anr()

        # Count errors
        error_count = sum(1 for issue in self.issues if issue.type == StabilityIssueType.ERROR)

        return StabilityStatus(
            is_running=is_running,
            has_crashed=has_crashed,
            has_anr=has_anr,
            error_count=error_count,
        )

    def get_issues(self) -> List[StabilityIssue]:
        """Get all detected stability issues."""
        return self.issues

    def stop_monitoring(self) -> StabilityReport:
        """Stop monitoring and return complete report."""
        self._monitoring = False

        # Count issues by type
        crashes = sum(1 for issue in self.issues if issue.type == StabilityIssueType.CRASH)
        anrs = sum(1 for issue in self.issues if issue.type == StabilityIssueType.ANR)
        errors = sum(1 for issue in self.issues if issue.type == StabilityIssueType.ERROR)

        return StabilityReport(
            total_crashes=crashes,
            total_anrs=anrs,
            total_errors=errors,
            issues=self.issues,
        )

    def _is_app_running(self) -> bool:
        """Check if app is currently running."""
        if not self.app_package:
            return False

        try:
            result = subprocess.run(
                f"{self.adb_path} -s {self.device_id} shell pidof {self.app_package}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 and len(result.stdout.strip()) > 0
        except Exception:
            return False

    def _check_for_crash(self) -> bool:
        """Check for recent crashes in logcat."""
        if not self.app_package:
            return False

        try:
            # Check recent logcat for crash indicators
            result = subprocess.run(
                f"{self.adb_path} -s {self.device_id} logcat -d -t 100 | grep -i 'FATAL\\|crash\\|exception'",
                shell=True,
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0 and self.app_package in result.stdout:
                # Found crash indicator for our app
                # Extract crash message
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if self.app_package in line and ('FATAL' in line or 'crash' in line.lower()):
                        issue = StabilityIssue(
                            type=StabilityIssueType.CRASH,
                            timestamp=datetime.now(),
                            message=line.strip(),
                            severity="critical",
                        )
                        # Only add if not already recorded
                        if not any(i.message == issue.message for i in self.issues):
                            self.issues.append(issue)
                        return True

        except Exception:
            pass

        return False

    def _check_for_anr(self) -> bool:
        """Check for ANR in logcat."""
        if not self.app_package:
            return False

        try:
            # Check recent logcat for ANR indicators
            result = subprocess.run(
                f"{self.adb_path} -s {self.device_id} logcat -d -t 100 | grep -i 'ANR'",
                shell=True,
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0 and self.app_package in result.stdout:
                # Found ANR for our app
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if self.app_package in line and 'ANR' in line:
                        issue = StabilityIssue(
                            type=StabilityIssueType.ANR,
                            timestamp=datetime.now(),
                            message=line.strip(),
                            severity="high",
                        )
                        # Only add if not already recorded
                        if not any(i.message == issue.message for i in self.issues):
                            self.issues.append(issue)
                        return True

        except Exception:
            pass

        return False
