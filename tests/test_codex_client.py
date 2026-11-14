"""Tests for CodexAppServerClient initialization and protocol calls."""

from unittest.mock import MagicMock, patch

import pytest

from coderelay.codex_client import CodexAppServerClient
from coderelay.exceptions import CodexProcessError


def test_client_handshake_and_fetch():
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()

    with patch("coderelay.codex_client.locate_codex_cli", return_value="C:\\fake\\codex.cmd"), \
         patch("coderelay.codex_client.start_codex_app_server", return_value=mock_proc), \
         patch("coderelay.codex_client.JsonRpcClient") as mock_rpc_cls:

        mock_rpc = MagicMock()
        mock_rpc.is_alive = True
        mock_rpc_cls.return_value = mock_rpc

        # Handshake response
        mock_rpc.request.side_effect = [
            {"capabilities": {}},  # initialize
            {"account": {"planType": "pro"}},  # account/read
            {"rateLimitsByLimitId": {"codex": {"primary": {"usedPercent": 50}}}},  # rate limits
        ]

        client = CodexAppServerClient(codex_command="codex")
        client.start()

        # Check initialize was called with request_id=0
        mock_rpc.request.assert_any_call(
            "initialize",
            params={
                "clientInfo": {
                    "name": "coderelay",
                    "title": "CodeRelay",
                    "version": "0.1.0",
                }
            },
            timeout=12.0,
            request_id=0,
        )
        # Check initialized notification was sent
        mock_rpc.notify.assert_called_once_with("initialized", params={})

        # Test fetch account
        acct = client.fetch_account_info()
        assert acct.plan_type == "pro"

        # Test fetch rate limits
        snapshot = client.fetch_rate_limits()
        assert snapshot.highest_usage_percent == 50.0

        client.stop()
        mock_rpc.close.assert_called_once()


def test_client_start_failure_cleans_up():
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()

    with patch("coderelay.codex_client.locate_codex_cli", return_value="C:\\fake\\codex.cmd"), \
         patch("coderelay.codex_client.start_codex_app_server", return_value=mock_proc), \
         patch("coderelay.codex_client.JsonRpcClient") as mock_rpc_cls, \
         patch("coderelay.codex_client.terminate_codex_process") as mock_term:

        mock_rpc = MagicMock()
        mock_rpc_cls.return_value = mock_rpc
        mock_rpc.request.side_effect = RuntimeError("Handshake failed")

        client = CodexAppServerClient(codex_command="codex")
        with pytest.raises(RuntimeError, match="Handshake failed"):
            client.start()

        mock_term.assert_called_once()


def test_client_reconnect_cleans_up_stale_process():
    """Verify that start() stops a stale process before launching a new one."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()

    with patch("coderelay.codex_client.locate_codex_cli", return_value="C:\\fake\\codex.cmd"), \
         patch("coderelay.codex_client.start_codex_app_server", return_value=mock_proc), \
         patch("coderelay.codex_client.JsonRpcClient") as mock_rpc_cls, \
         patch("coderelay.codex_client.terminate_codex_process") as mock_term:

        mock_rpc = MagicMock()
        mock_rpc.is_alive = True
        mock_rpc_cls.return_value = mock_rpc
        mock_rpc.request.return_value = {"capabilities": {}}

        client = CodexAppServerClient(codex_command="codex")
        client.start()

        # Simulate connection dying: rpc is dead but objects still set
        mock_rpc.is_alive = False
        assert client.is_connected is False
        assert client._rpc is not None  # stale reference still present

        # Reconnect — should clean up the stale process first
        mock_rpc2 = MagicMock()
        mock_rpc2.is_alive = True
        mock_rpc2.request.return_value = {"capabilities": {}}
        mock_rpc_cls.return_value = mock_rpc2

        client.start()

        # The old process should have been terminated during cleanup
        mock_term.assert_called()
        assert client._rpc is mock_rpc2
