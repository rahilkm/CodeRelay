"""Tests for persistent UsageMonitor service."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, PropertyMock, patch

from coderelay.codex_client import CodexAppServerClient
from coderelay.config import Config, MonitoringConfig, WorkspaceConfig
from coderelay.monitor import UsageMonitor
from coderelay.usage_models import (
    AccountInfo,
    ThresholdEvent,
    ThresholdLevel,
    UsageBucket,
    UsageSnapshot,
    UsageWindow,
)


def make_test_config() -> Config:
    return Config(
        workspace=WorkspaceConfig(path="."),
        monitoring=MonitoringConfig(
            interval_seconds=60,
            recommend_threshold=90.0,
            critical_threshold=95.0,
        ),
    )


def make_snapshot(usage: float) -> UsageSnapshot:
    bucket = UsageBucket(
        limit_id="codex",
        primary=UsageWindow(name="primary", used_percent=usage),
    )
    return UsageSnapshot(
        timestamp=datetime.now(timezone.utc).astimezone(),
        account=AccountInfo(plan_type="pro"),
        buckets={"codex": bucket},
    )


def test_monitor_single_poll_and_events():
    cfg = make_test_config()
    snapshots_received = []
    events_received = []

    monitor = UsageMonitor(
        config=cfg,
        on_snapshot=snapshots_received.append,
        on_threshold_event=events_received.append,
    )

    with patch.object(CodexAppServerClient, "is_connected", new_callable=PropertyMock, return_value=True), \
         patch.object(monitor.client, "fetch_usage_snapshot") as mock_fetch:

        # 1. Normal usage
        mock_fetch.return_value = make_snapshot(50.0)
        snap1 = monitor.poll_once()
        assert snap1 is not None
        assert len(snapshots_received) == 1
        assert len(events_received) == 0

        # 2. Usage reaches 90% recommendation
        mock_fetch.return_value = make_snapshot(91.0)
        snap2 = monitor.poll_once()
        assert len(events_received) == 1
        assert events_received[0].level == ThresholdLevel.RECOMMEND

        # 3. Usage reaches 96% critical
        mock_fetch.return_value = make_snapshot(96.0)
        snap3 = monitor.poll_once()
        assert len(events_received) == 2
        assert events_received[1].level == ThresholdLevel.CRITICAL


def test_monitor_recovery_on_server_error():
    cfg = make_test_config()
    status_messages = []

    monitor = UsageMonitor(
        config=cfg,
        on_status_message=status_messages.append,
    )

    with patch.object(CodexAppServerClient, "is_connected", new_callable=PropertyMock, return_value=True), \
         patch.object(monitor.client, "fetch_usage_snapshot", side_effect=RuntimeError("Pipe broken")), \
         patch.object(monitor.client, "stop") as mock_stop:

        snap = monitor.poll_once()
        assert snap is None
        mock_stop.assert_called_once()
        assert any("Monitoring error: Pipe broken" in msg for msg in status_messages)


def test_monitor_run_and_clean_stop():
    cfg = make_test_config()
    monitor = UsageMonitor(config=cfg)

    with patch.object(monitor, "poll_once") as mock_poll:
        def stop_on_poll():
            monitor.stop()

        mock_poll.side_effect = stop_on_poll
        monitor.run()

        assert monitor.is_running is False
        mock_poll.assert_called_once()
