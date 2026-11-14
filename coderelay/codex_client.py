"""Codex App Server protocol client managing initialization, account, and rate limits."""

from __future__ import annotations

import logging
import subprocess
from typing import Any, Optional

from coderelay import __version__
from coderelay.exceptions import CodexProcessError
from coderelay.process import locate_codex_cli, start_codex_app_server, terminate_codex_process
from coderelay.rpc import JsonRpcClient
from coderelay.usage_models import AccountInfo, UsageSnapshot
from coderelay.usage_parser import parse_account_info, parse_rate_limits

logger = logging.getLogger(__name__)


class CodexAppServerClient:
    """Manages the lifecycle and protocol requests for a Codex App Server instance."""

    def __init__(
        self,
        codex_command: Optional[str] = None,
        request_timeout: float = 12.0,
    ) -> None:
        self.codex_command = codex_command
        self.request_timeout = request_timeout

        self._process: Optional[subprocess.Popen] = None
        self._rpc: Optional[JsonRpcClient] = None
        self._cached_account: Optional[AccountInfo] = None

    @property
    def is_connected(self) -> bool:
        """Check if process and RPC client are both active and healthy."""
        return (
            self._process is not None
            and self._process.poll() is None
            and self._rpc is not None
            and self._rpc.is_alive
        )

    def start(self) -> None:
        """Start the App Server process and execute the initial protocol handshake."""
        if self.is_connected:
            return

        # Clean up any stale process/rpc from a previous failed connection
        # before starting a new one, to avoid leaking processes.
        if self._rpc is not None or self._process is not None:
            self.stop()

        codex_path = locate_codex_cli(self.codex_command)
        self._process = start_codex_app_server(codex_path)

        if self._process.stdin is None or self._process.stdout is None:
            terminate_codex_process(self._process)
            raise CodexProcessError("Codex App Server standard I/O pipes were not opened.")

        self._rpc = JsonRpcClient(
            stdin=self._process.stdin,
            stdout=self._process.stdout,
        )

        try:
            self._perform_handshake()
        except Exception:
            self.stop()
            raise

    def _perform_handshake(self) -> None:
        """Execute the verified initialize -> initialized sequence."""
        assert self._rpc is not None

        # 1. initialize request with id=0 (matches verified script)
        init_params = {
            "clientInfo": {
                "name": "coderelay",
                "title": "CodeRelay",
                "version": __version__,
            }
        }
        self._rpc.request("initialize", params=init_params, timeout=self.request_timeout, request_id=0)

        # 2. initialized notification
        self._rpc.notify("initialized", params={})

    def fetch_account_info(self, refresh_token: bool = False) -> AccountInfo:
        """Fetch authenticated account info using account/read."""
        if not self.is_connected:
            raise CodexProcessError("Codex App Server is not connected.")
        assert self._rpc is not None

        result = self._rpc.request(
            "account/read",
            params={"refreshToken": refresh_token},
            timeout=self.request_timeout,
        )
        account = parse_account_info(result)
        self._cached_account = account
        return account

    def fetch_rate_limits(self) -> UsageSnapshot:
        """Fetch current rate limits using account/rateLimits/read."""
        if not self.is_connected:
            raise CodexProcessError("Codex App Server is not connected.")
        assert self._rpc is not None

        result = self._rpc.request(
            "account/rateLimits/read",
            params={},
            timeout=self.request_timeout,
        )
        snapshot = parse_rate_limits(result, account=self._cached_account)
        if snapshot.account and snapshot.account.plan_type != "Unknown":
            self._cached_account = snapshot.account
        return snapshot

    def fetch_usage_snapshot(self) -> UsageSnapshot:
        """Ensure account information is known and fetch current usage snapshot."""
        if self._cached_account is None or self._cached_account.plan_type == "Unknown":
            try:
                self.fetch_account_info()
            except Exception as e:
                logger.debug("Failed to read account info: %s", e)

        return self.fetch_rate_limits()

    def stop(self) -> None:
        """Cleanly close RPC connection and terminate server process tree."""
        if self._rpc is not None:
            try:
                self._rpc.close()
            except Exception:
                pass
            self._rpc = None

        if self._process is not None:
            terminate_codex_process(self._process)
            self._process = None
