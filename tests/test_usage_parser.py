"""Tests for Codex account and rate-limit parsing."""

from datetime import datetime, timezone

from coderelay.usage_models import AccountInfo
from coderelay.usage_parser import (
    parse_account_info,
    parse_rate_limits,
    parse_timestamp,
    parse_usage_bucket,
    parse_usage_window,
)


def test_parse_timestamp():
    # Unix seconds
    dt = parse_timestamp(1700000000)
    assert dt is not None
    assert dt.timestamp() == 1700000000

    # Unix milliseconds
    dt_ms = parse_timestamp(1700000000000)
    assert dt_ms is not None
    assert dt_ms.timestamp() == 1700000000

    # None and invalid
    assert parse_timestamp(None) is None
    assert parse_timestamp("invalid") is None
    assert parse_timestamp({}) is None


def test_parse_account_info_nested_and_safety():
    raw = {
        "account": {
            "type": "chatgpt",
            "email": "user@example.com",
            "planType": "pro",
            "workspaceName": "personal",
        },
        "userId": "user-secret-123",
        "requiresOpenaiAuth": True,
        "workspaceRouting": {
            "chatgptAccountId": "acc-secret-456",
        },
    }
    acct = parse_account_info(raw)
    assert acct.plan_type == "pro"
    assert acct.is_authenticated is True
    # Ensure sensitive fields are not stored in AccountInfo
    assert not hasattr(acct, "email")
    assert not hasattr(acct, "userId")
    assert not hasattr(acct, "chatgptAccountId")


def test_parse_account_info_missing_fields():
    assert parse_account_info({}).plan_type == "Unknown"
    assert parse_account_info(None).plan_type == "Unknown"
    assert parse_account_info({"planType": "plus"}).plan_type == "plus"


def test_parse_rate_limits_standard_codex():
    raw = {
        "rateLimitsByLimitId": {
            "codex": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 42.5,
                    "windowDurationMins": 43200,
                    "resetsAt": 1797268192,
                },
                "secondary": None,
                "planType": "go",
                "rateLimitReachedType": None,
            }
        }
    }
    snapshot = parse_rate_limits(raw)
    assert snapshot.account.plan_type == "go"
    assert "codex" in snapshot.buckets
    bucket = snapshot.buckets["codex"]
    assert bucket.primary is not None
    assert bucket.primary.used_percent == 42.5
    assert bucket.primary.remaining_percent == 57.5
    assert bucket.primary.resets_at is not None
    assert bucket.secondary is None
    assert bucket.highest_used_percent == 42.5
    assert snapshot.highest_usage_percent == 42.5
    assert bucket.is_rate_limited is False


def test_parse_rate_limits_multiple_buckets_and_windows():
    raw = {
        "rateLimitsByLimitId": {
            "standard": {
                "limitId": "standard",
                "primary": {"usedPercent": 30.0},
                "secondary": {"usedPercent": 85.0},
            },
            "preview": {
                "limitId": "preview",
                "primary": {"usedPercent": 92.0},
                "secondary": None,
                "rateLimitReachedType": "LIMIT_EXCEEDED",
            },
        }
    }
    snapshot = parse_rate_limits(raw)
    assert len(snapshot.buckets) == 2
    assert snapshot.highest_usage_percent == 92.0
    assert snapshot.buckets["preview"].is_rate_limited is True
    assert snapshot.buckets["standard"].highest_used_percent == 85.0


def test_parse_rate_limits_single_fallback():
    raw = {
        "rateLimits": {
            "limitId": "default",
            "primary": {"usedPercent": 15},
            "secondary": {"usedPercent": 25},
        }
    }
    snapshot = parse_rate_limits(raw)
    assert "default" in snapshot.buckets
    assert snapshot.highest_usage_percent == 25.0


def test_parse_rate_limits_missing_optional_fields():
    raw = {
        "rateLimits": {
            "primary": None,
            "secondary": None,
        }
    }
    snapshot = parse_rate_limits(raw)
    assert snapshot.highest_usage_percent == 0.0
    assert snapshot.primary_window is None
    assert snapshot.secondary_window is None


def test_parse_malformed_rate_limits():
    snapshot = parse_rate_limits("not-a-dict")
    assert snapshot.highest_usage_percent == 0.0
    assert len(snapshot.buckets) == 0
