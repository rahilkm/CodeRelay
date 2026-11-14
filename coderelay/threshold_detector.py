"""Threshold detection, alert suppression, and state management."""

from __future__ import annotations

from datetime import datetime, timezone

from coderelay.usage_models import (
    ThresholdEvent,
    ThresholdLevel,
    UsageSnapshot,
)


class ThresholdDetector:
    """Evaluates usage snapshots against configured thresholds with duplicate suppression."""

    def __init__(
        self,
        recommend_threshold: float = 90.0,
        critical_threshold: float = 95.0,
    ) -> None:
        self.recommend_threshold = recommend_threshold
        self.critical_threshold = critical_threshold

        self._recommend_emitted: bool = False
        self._critical_emitted: bool = False
        self._last_highest_usage: float = 0.0

    @property
    def recommend_emitted(self) -> bool:
        return self._recommend_emitted

    @property
    def critical_emitted(self) -> bool:
        return self._critical_emitted

    def reset(self) -> None:
        """Reset emitted states (e.g. for testing or cycle reset)."""
        self._recommend_emitted = False
        self._critical_emitted = False
        self._last_highest_usage = 0.0

    def evaluate(self, snapshot: UsageSnapshot) -> ThresholdEvent | None:
        """Evaluate a snapshot and return a ThresholdEvent if a threshold is newly breached.

        Returns None if usage is below thresholds or if the event has already been emitted
        for the current usage cycle.
        """
        usage = snapshot.highest_usage_percent
        now = snapshot.timestamp or datetime.now(timezone.utc).astimezone()

        # If usage dropped below the recommendation threshold, reset alert states for a new cycle
        if usage < self.recommend_threshold and (self._recommend_emitted or self._critical_emitted):
            self._recommend_emitted = False
            self._critical_emitted = False

        self._last_highest_usage = usage

        # Find the specific bucket/window causing the highest usage for detailed context
        active_bucket_id = None
        active_window_name = None
        for b_id, bucket in snapshot.buckets.items():
            for w in bucket.all_windows:
                if w.used_percent == usage:
                    active_bucket_id = b_id
                    active_window_name = w.name
                    break
            if active_bucket_id:
                break

        # Check critical threshold (e.g. 95%)
        if usage >= self.critical_threshold:
            if not self._critical_emitted:
                self._critical_emitted = True
                self._recommend_emitted = True
                return ThresholdEvent(
                    level=ThresholdLevel.CRITICAL,
                    used_percent=usage,
                    threshold=self.critical_threshold,
                    timestamp=now,
                    bucket_id=active_bucket_id,
                    window_name=active_window_name,
                    message=(
                        f"CRITICAL: Account usage has reached {usage:.1f}% "
                        f"(threshold: {self.critical_threshold:.1f}%). "
                        "Second handoff recommendation: switch to Antigravity immediately."
                    ),
                )
            return None

        # Check recommend threshold (e.g. 90%)
        if usage >= self.recommend_threshold:
            if not self._recommend_emitted:
                self._recommend_emitted = True
                return ThresholdEvent(
                    level=ThresholdLevel.RECOMMEND,
                    used_percent=usage,
                    threshold=self.recommend_threshold,
                    timestamp=now,
                    bucket_id=active_bucket_id,
                    window_name=active_window_name,
                    message=(
                        f"RECOMMENDATION: Account usage has reached {usage:.1f}% "
                        f"(threshold: {self.recommend_threshold:.1f}%). "
                        "Consider handing off to Antigravity."
                    ),
                )
            return None

        return None
