"""Tests for threshold detection and alert suppression."""

from datetime import datetime, timezone

from coderelay.threshold_detector import ThresholdDetector
from coderelay.usage_models import (
    AccountInfo,
    ThresholdLevel,
    UsageBucket,
    UsageSnapshot,
    UsageWindow,
)


def make_snapshot(used_percent: float) -> UsageSnapshot:
    bucket = UsageBucket(
        limit_id="codex",
        primary=UsageWindow(name="primary", used_percent=used_percent),
    )
    return UsageSnapshot(
        timestamp=datetime.now(timezone.utc).astimezone(),
        account=AccountInfo(plan_type="test"),
        buckets={"codex": bucket},
    )


def test_threshold_detector_below_threshold():
    detector = ThresholdDetector(recommend_threshold=90.0, critical_threshold=95.0)
    event = detector.evaluate(make_snapshot(50.0))
    assert event is None
    assert detector.recommend_emitted is False
    assert detector.critical_emitted is False


def test_threshold_detector_90_percent_recommendation():
    detector = ThresholdDetector(recommend_threshold=90.0, critical_threshold=95.0)

    # 1. Breaches 90%
    event = detector.evaluate(make_snapshot(90.5))
    assert event is not None
    assert event.level == ThresholdLevel.RECOMMEND
    assert event.used_percent == 90.5
    assert detector.recommend_emitted is True
    assert detector.critical_emitted is False

    # 2. Duplicate suppression at 90%+
    event_dup = detector.evaluate(make_snapshot(91.0))
    assert event_dup is None


def test_threshold_detector_95_percent_critical():
    detector = ThresholdDetector(recommend_threshold=90.0, critical_threshold=95.0)

    # First event at 90%
    detector.evaluate(make_snapshot(90.0))
    assert detector.recommend_emitted is True

    # Critical event at 95%
    event_crit = detector.evaluate(make_snapshot(95.2))
    assert event_crit is not None
    assert event_crit.level == ThresholdLevel.CRITICAL
    assert event_crit.used_percent == 95.2
    assert detector.critical_emitted is True

    # Duplicate suppression at 95%+
    event_crit_dup = detector.evaluate(make_snapshot(97.0))
    assert event_crit_dup is None


def test_threshold_detector_jump_straight_to_95():
    detector = ThresholdDetector(recommend_threshold=90.0, critical_threshold=95.0)
    event = detector.evaluate(make_snapshot(96.0))
    assert event is not None
    assert event.level == ThresholdLevel.CRITICAL
    assert detector.critical_emitted is True
    assert detector.recommend_emitted is True


def test_threshold_detector_reset_on_usage_drop():
    detector = ThresholdDetector(recommend_threshold=90.0, critical_threshold=95.0)

    # Hit 90% and 95%
    detector.evaluate(make_snapshot(96.0))
    assert detector.critical_emitted is True

    # Usage drops below 90% (e.g. usage reset)
    detector.evaluate(make_snapshot(5.0))
    assert detector.recommend_emitted is False
    assert detector.critical_emitted is False

    # Next cycle reaching 90% triggers event again
    event_new = detector.evaluate(make_snapshot(91.0))
    assert event_new is not None
    assert event_new.level == ThresholdLevel.RECOMMEND
