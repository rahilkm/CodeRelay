"""Robust parsing for Codex account and rate-limit JSON responses."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from coderelay.usage_models import (
    AccountInfo,
    UsageBucket,
    UsageSnapshot,
    UsageWindow,
)


def parse_timestamp(val: Any) -> datetime | None:
    """Parse a timestamp which may be unix seconds, milliseconds, or ISO string."""
    if val is None:
        return None

    if isinstance(val, (int, float)):
        try:
            # If greater than 1e11, it is almost certainly milliseconds
            ts_seconds = val / 1000.0 if val > 1e11 else float(val)
            return datetime.fromtimestamp(ts_seconds, tz=timezone.utc).astimezone()
        except (ValueError, OverflowError, OSError):
            return None

    if isinstance(val, str) and val.strip():
        # Try ISO format
        try:
            return datetime.fromisoformat(val).astimezone()
        except (ValueError, TypeError):
            # Try parsing numeric string
            try:
                num = float(val)
                ts_sec = num / 1000.0 if num > 1e11 else num
                return datetime.fromtimestamp(ts_sec, tz=timezone.utc).astimezone()
            except (ValueError, OverflowError, OSError):
                return None

    return None


def parse_account_info(data: Any) -> AccountInfo:
    """Extract account plan type safely without retaining sensitive fields."""
    if not isinstance(data, dict):
        return AccountInfo(plan_type="Unknown", is_authenticated=False)

    # Search for planType in various known response locations
    plan_type: str | None = None

    if "planType" in data and isinstance(data["planType"], str) and data["planType"].strip():
        plan_type = data["planType"].strip()
    elif "account" in data and isinstance(data["account"], dict):
        acct = data["account"]
        if "planType" in acct and isinstance(acct["planType"], str) and acct["planType"].strip():
            plan_type = acct["planType"].strip()
    elif "user" in data and isinstance(data["user"], dict):
        user = data["user"]
        if "planType" in user and isinstance(user["planType"], str) and user["planType"].strip():
            plan_type = user["planType"].strip()

    is_authenticated = bool(data.get("requiresOpenaiAuth") is not False)
    if "account" in data and data["account"] is None:
        is_authenticated = False

    return AccountInfo(
        plan_type=plan_type or "Unknown",
        is_authenticated=is_authenticated,
    )


def parse_usage_window(name: str, data: Any) -> UsageWindow | None:
    """Safely parse a single usage window dictionary."""
    if not isinstance(data, dict):
        return None

    raw_used = data.get("usedPercent")
    if raw_used is None:
        return None

    try:
        used_percent = float(raw_used)
    except (ValueError, TypeError):
        return None

    resets_at = parse_timestamp(data.get("resetsAt"))
    duration = data.get("windowDurationMins")
    duration_mins = int(duration) if isinstance(duration, (int, float)) else None

    return UsageWindow(
        name=name,
        used_percent=used_percent,
        resets_at=resets_at,
        window_duration_mins=duration_mins,
    )


def parse_usage_bucket(limit_id: str, data: Any) -> UsageBucket:
    """Safely parse a bucket of rate-limit windows."""
    if not isinstance(data, dict):
        return UsageBucket(limit_id=limit_id)

    primary = parse_usage_window("primary", data.get("primary"))
    secondary = parse_usage_window("secondary", data.get("secondary"))

    # Also check other potential window keys
    other_windows: dict[str, UsageWindow] = {}
    standard_keys = {
        "primary",
        "secondary",
        "limitId",
        "limitName",
        "normalModelSlug",
        "credits",
        "individualLimit",
        "spendControlReached",
        "planType",
        "rateLimitReachedType",
    }
    for key, val in data.items():
        if key not in standard_keys and isinstance(val, dict) and "usedPercent" in val:
            w = parse_usage_window(key, val)
            if w is not None:
                other_windows[key] = w

    return UsageBucket(
        limit_id=limit_id,
        primary=primary,
        secondary=secondary,
        other_windows=other_windows,
        rate_limit_reached_type=data.get("rateLimitReachedType"),
        spend_control_reached=bool(data.get("spendControlReached", False)),
        plan_type=data.get("planType"),
    )


def parse_rate_limits(
    data: Any,
    account: AccountInfo | None = None,
    now: datetime | None = None,
) -> UsageSnapshot:
    """Safely parse the full account/rateLimits/read response."""
    timestamp = now or datetime.now(timezone.utc).astimezone()

    if account is None:
        account = parse_account_info(data if isinstance(data, dict) else {})

    if not isinstance(data, dict):
        return UsageSnapshot(timestamp=timestamp, account=account, buckets={})

    buckets: dict[str, UsageBucket] = {}

    # Check rateLimitsByLimitId first
    limits_by_id = data.get("rateLimitsByLimitId")
    if isinstance(limits_by_id, dict) and limits_by_id:
        for limit_id, bucket_data in limits_by_id.items():
            buckets[str(limit_id)] = parse_usage_bucket(str(limit_id), bucket_data)
    elif "rateLimits" in data:
        # Fallback to single rateLimits object or list
        raw_limits = data["rateLimits"]
        if isinstance(raw_limits, dict):
            limit_id = str(raw_limits.get("limitId") or "default")
            buckets[limit_id] = parse_usage_bucket(limit_id, raw_limits)
        elif isinstance(raw_limits, list):
            for idx, item in enumerate(raw_limits):
                if isinstance(item, dict):
                    limit_id = str(item.get("limitId") or f"bucket_{idx}")
                    buckets[limit_id] = parse_usage_bucket(limit_id, item)

    # If planType was unknown from account/read, try finding it in rate limits
    if account.plan_type == "Unknown":
        for b in buckets.values():
            if b.plan_type:
                account = AccountInfo(plan_type=b.plan_type, is_authenticated=account.is_authenticated)
                break

    return UsageSnapshot(
        timestamp=timestamp,
        account=account,
        buckets=buckets,
    )
