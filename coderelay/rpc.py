"""Thread-safe JSON-RPC client with response-ID matching and timeout handling."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Optional, TextIO

from coderelay.exceptions import (
    JsonRpcConnectionClosedError,
    JsonRpcError,
    JsonRpcTimeoutError,
)

logger = logging.getLogger(__name__)


class _PendingRequest:
    __slots__ = ("event", "result", "error", "done")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None
        self.error: Any = None
        self.done: bool = False


class JsonRpcClient:
    """Thread-safe JSON-RPC client for bidirectional stream communication."""

    def __init__(self, stdin: TextIO, stdout: TextIO) -> None:
        self._stdin = stdin
        self._stdout = stdout

        self._lock = threading.Lock()
        self._id_counter = 0
        self._pending: dict[int, _PendingRequest] = {}
        self._notification_handlers: list[Callable[[str, Any], None]] = []

        self._closed = threading.Event()
        self._termination_error: Optional[Exception] = None

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="CodexJsonRpcReader",
            daemon=True,
        )
        self._reader_thread.start()

    @property
    def is_alive(self) -> bool:
        return not self._closed.is_set()

    def register_notification_handler(
        self, handler: Callable[[str, Any], None]
    ) -> None:
        """Register a callback for JSON-RPC notifications."""
        with self._lock:
            self._notification_handlers.append(handler)

    def _reader_loop(self) -> None:
        """Background thread reading and demultiplexing lines from stdout."""
        try:
            while not self._closed.is_set():
                line = self._stdout.readline()
                if not line:
                    # Output stream closed (server terminated)
                    break

                line_str = line.strip()
                if not line_str:
                    continue

                try:
                    message = json.loads(line_str)
                except json.JSONDecodeError:
                    # Ignore malformed or non-JSON output gracefully
                    continue

                if not isinstance(message, dict):
                    continue

                # Case 1: Response with an ID
                if "id" in message and message["id"] is not None:
                    req_id = message["id"]
                    req: Optional[_PendingRequest] = None
                    with self._lock:
                        req = self._pending.pop(req_id, None)

                    if req is not None:
                        if "error" in message:
                            req.error = message["error"]
                        else:
                            req.result = message.get("result", {})
                        req.done = True
                        req.event.set()

                # Case 2: Server notification (has method, no id)
                elif "method" in message:
                    method_name = str(message.get("method", ""))
                    params = message.get("params", {})
                    with self._lock:
                        handlers = list(self._notification_handlers)
                    for handler in handlers:
                        try:
                            handler(method_name, params)
                        except Exception as e:
                            logger.debug("Notification handler error: %s", e)

        except Exception as err:
            self._termination_error = err
        finally:
            self._handle_disconnect()

    def _handle_disconnect(self) -> None:
        """Mark as closed and unblock any pending requests."""
        self._closed.set()
        if self._termination_error is None:
            self._termination_error = JsonRpcConnectionClosedError(
                "Codex App Server closed its output stream."
            )

        with self._lock:
            pending_items = list(self._pending.values())
            self._pending.clear()

        for req in pending_items:
            req.error = str(self._termination_error)
            req.done = True
            req.event.set()

    def request(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
        timeout: float = 10.0,
        request_id: Optional[int] = None,
    ) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        if self._closed.is_set():
            raise self._termination_error or JsonRpcConnectionClosedError(
                "Cannot send request: connection is closed."
            )

        req = _PendingRequest()
        with self._lock:
            if request_id is not None:
                req_id = request_id
                if req_id >= self._id_counter:
                    self._id_counter = req_id + 1
            else:
                req_id = self._id_counter
                self._id_counter += 1

            self._pending[req_id] = req

            payload = {
                "method": method,
                "id": req_id,
                "params": params if params is not None else {},
            }
            try:
                self._stdin.write(json.dumps(payload) + "\n")
                self._stdin.flush()
            except Exception as err:
                self._pending.pop(req_id, None)
                raise JsonRpcConnectionClosedError(f"Failed to write to Codex App Server: {err}") from err

        finished = req.event.wait(timeout=timeout)
        if not finished:
            with self._lock:
                self._pending.pop(req_id, None)
            raise JsonRpcTimeoutError(
                f"JSON-RPC request '{method}' (id={req_id}) timed out after {timeout} seconds."
            )

        if req.error is not None:
            raise JsonRpcError(f"Codex App Server error for '{method}': {req.error}")

        return req.result

    def notify(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
    ) -> None:
        """Send a JSON-RPC notification (no ID, no response expected)."""
        if self._closed.is_set():
            raise self._termination_error or JsonRpcConnectionClosedError(
                "Cannot send notification: connection is closed."
            )

        payload = {
            "method": method,
            "params": params if params is not None else {},
        }
        with self._lock:
            try:
                self._stdin.write(json.dumps(payload) + "\n")
                self._stdin.flush()
            except Exception as err:
                raise JsonRpcConnectionClosedError(
                    f"Failed to send notification to Codex App Server: {err}"
                ) from err

    def close(self) -> None:
        """Close client and terminate reader."""
        self._closed.set()
        with self._lock:
            pending_items = list(self._pending.values())
            self._pending.clear()

        for req in pending_items:
            req.error = "Client closed."
            req.done = True
            req.event.set()
