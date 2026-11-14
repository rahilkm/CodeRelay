"""Data models for Codex account usage, windows, and threshold events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ThresholdLevel(str, Enum):
    """Usage threshold alert levels."""

    NONE = "none"
    RECOMMEND = "recommend"  # Reached recommend threshold (e.g. 90%)
    CRITICAL = "critical"    # Reached critical threshold (e.g. 95%)


@dataclass(slots=True)
class UsageWindow:
    """A specific rate-limit measurement window (e.g. primary, secondary)."""

    name: str
    used_percent: float
    resets_at: datetime | None = None
    window_duration_mins: int | None = None

    @property
    def remaining_percent(self) -> float:
        return max(0.0, round(100.0 - self.used_percent, 2))


@dataclass(slots=True)
class UsageBucket:
    """A collection of rate limit windows for a limit identifier."""

    limit_id: str
    primary: UsageWindow | None = None
    secondary: UsageWindow | None = None
    other_windows: dict[str, UsageWindow] = field(default_factory=dict)
    rate_limit_reached_type: str | None = None
    spend_control_reached: bool = False
    plan_type: str | None = None

    @property
    def all_windows(self) -> list[UsageWindow]:
        windows = []
        if self.primary is not None:
            windows.append(self.primary)
        if self.secondary is not None:
            windows.append(self.secondary)
        windows.extend(self.other_windows.values())
        return windows

    @property
    def highest_used_percent(self) -> float:
        windows = self.all_windows
        if not windows:
            return 0.0
        return max(w.used_percent for w in windows)

    @property
    def is_rate_limited(self) -> bool:
        return bool(self.rate_limit_reached_type or self.spend_control_reached)


@dataclass(slots=True)
class AccountInfo:
    """Safe account information container with sensitive details omitted."""

    plan_type: str = "Unknown"
    is_authenticated: bool = True


@dataclass(slots=True)
class UsageSnapshot:
    """A point-in-time snapshot of account rate-limits and usage."""

    timestamp: datetime
    account: AccountInfo
    buckets: dict[str, UsageBucket] = field(default_factory=dict)

    @property
    def highest_usage_percent(self) -> float:
        if not self.buckets:
            return 0.0
        return max((b.highest_used_percent for b in self.buckets.values()), default=0.0)

    @property
    def primary_bucket(self) -> UsageBucket | None:
        if not self.buckets:
            return None
        if "codex" in self.buckets:
            return self.buckets["codex"]
        return next(iter(self.buckets.values()))

    @property
    def primary_window(self) -> UsageWindow | None:
        bucket = self.primary_bucket
        return bucket.primary if bucket else None

    @property
    def secondary_window(self) -> UsageWindow | None:
        bucket = self.primary_bucket
        return bucket.secondary if bucket else None


@dataclass(slots=True)
class ThresholdEvent:
    """Event emitted when account usage reaches or exceeds a configured threshold."""

    level: ThresholdLevel
    used_percent: float
    threshold: float
    timestamp: datetime
    message: str
    bucket_id: str | None = None
    window_name: str | None = None
