"""Persistent usage monitoring service for Codex."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from coderelay.codex_client import CodexAppServerClient
from coderelay.config import Config
from coderelay.threshold_detector import ThresholdDetector
from coderelay.usage_models import ThresholdEvent, UsageSnapshot

logger = logging.getLogger(__name__)


class UsageMonitor:
    """Persistent service monitoring Codex account usage and evaluating thresholds."""

    def __init__(
        self,
        config: Config,
        on_snapshot: Optional[Callable[[UsageSnapshot], None]] = None,
        on_threshold_event: Optional[Callable[[ThresholdEvent], None]] = None,
        on_status_message: Optional[Callable[[str], None]] = None,
        max_recovery_attempts: int = 3,
    ) -> None:
        self.config = config
        self.interval_seconds = config.monitoring.interval_seconds
        self.on_snapshot = on_snapshot
        self.on_threshold_event = on_threshold_event
        self.on_status_message = on_status_message
        self.max_recovery_attempts = max_recovery_attempts

        self.detector = ThresholdDetector(
            recommend_threshold=config.monitoring.recommend_threshold,
            critical_threshold=config.monitoring.critical_threshold,
        )

        codex_cmd = config.primary_agent.command if config.primary_agent else "codex"
        self.client = CodexAppServerClient(codex_command=codex_cmd)

        self._stop_event = threading.Event()
        self._last_snapshot: Optional[UsageSnapshot] = None
        self._consecutive_errors = 0

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    @property
    def last_snapshot(self) -> Optional[UsageSnapshot]:
        return self._last_snapshot

    def _notify_status(self, msg: str) -> None:
        if self.on_status_message:
            try:
                self.on_status_message(msg)
            except Exception as e:
                logger.debug("Error in status message callback: %s", e)

    def _ensure_connected(self) -> None:
        """Ensure App Server process is running and connected, with recovery if needed."""
        if self.client.is_connected:
            return

        self._notify_status("Connecting to Codex App Server...")
        self.client.start()
        self._notify_status("Connected to Codex App Server.")

    def poll_once(self) -> Optional[UsageSnapshot]:
        """Execute a single polling cycle: fetch snapshot, evaluate thresholds, notify listeners."""
        try:
            self._ensure_connected()
            snapshot = self.client.fetch_usage_snapshot()
            self._last_snapshot = snapshot
            self._consecutive_errors = 0

            # Notify snapshot listener
            if self.on_snapshot:
                try:
                    self.on_snapshot(snapshot)
                except Exception as e:
                    logger.debug("Error in on_snapshot callback: %s", e)

            # Evaluate thresholds
            event = self.detector.evaluate(snapshot)
            if event and self.on_threshold_event:
                try:
                    self.on_threshold_event(event)
                except Exception as e:
                    logger.debug("Error in on_threshold_event callback: %s", e)

            return snapshot

        except Exception as err:
            self._consecutive_errors += 1
            self._notify_status(f"Monitoring error: {err}")
            # Reset client connection for recovery on next attempt
            try:
                self.client.stop()
            except Exception:
                pass
            return None

    def run(self) -> None:
        """Run the persistent monitoring loop until stop() is called."""
        self._stop_event.clear()
        self._notify_status(
            f"Starting Codex persistent monitor (polling interval: {self.interval_seconds}s)..."
        )

        try:
            while not self._stop_event.is_set():
                self.poll_once()

                # Sleep in short increments or wait on the stop event
                if self._stop_event.wait(timeout=self.interval_seconds):
                    break
        finally:
            self.stop()

    def stop(self) -> None:
        """Signal stop and cleanly terminate client and processes."""
        self._stop_event.set()
        try:
            self.client.stop()
        except Exception as e:
            logger.debug("Error stopping client: %s", e)
        self._notify_status("Codex usage monitor stopped.")
