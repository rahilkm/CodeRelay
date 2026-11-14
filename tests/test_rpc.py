"""Tests for thread-safe JSON-RPC client."""

import io
import json
import threading
import time

import pytest

from coderelay.exceptions import (
    JsonRpcConnectionClosedError,
    JsonRpcError,
    JsonRpcTimeoutError,
)
from coderelay.rpc import JsonRpcClient


class PipeIO:
    """In-memory thread-safe pipe for testing JSON-RPC communication."""

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._cv = threading.Condition()
        self._closed = False

    def write(self, s: str) -> int:
        with self._cv:
            if self._closed:
                raise OSError("Pipe closed")
            self._buffer.append(s)
            self._cv.notify_all()
            return len(s)

    def flush(self) -> None:
        pass

    def readline(self) -> str:
        with self._cv:
            while not self._buffer and not self._closed:
                self._cv.wait()
            if self._buffer:
                return self._buffer.pop(0)
            return ""

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()


def test_rpc_request_response_matching():
    client_in = PipeIO()   # App Server stdout -> client_in
    client_out = PipeIO()  # client_out -> App Server stdin

    client = JsonRpcClient(stdin=client_out, stdout=client_in)

    def mock_server():
        # Read request
        line = client_out.readline()
        req = json.loads(line)
        req_id = req["id"]
        # Reply
        resp = {"id": req_id, "result": {"status": "ok", "value": 42}}
        client_in.write(json.dumps(resp) + "\n")

    t = threading.Thread(target=mock_server)
    t.start()

    res = client.request("test_method", params={"foo": "bar"}, timeout=2.0)
    assert res == {"status": "ok", "value": 42}
    t.join()
    client.close()


def test_rpc_out_of_order_responses():
    client_in = PipeIO()
    client_out = PipeIO()
    client = JsonRpcClient(stdin=client_out, stdout=client_in)

    results = {}

    def mock_server():
        # Wait for two requests
        line1 = client_out.readline()
        line2 = client_out.readline()
        req1 = json.loads(line1)
        req2 = json.loads(line2)

        # Send req2 response first, then req1
        client_in.write(json.dumps({"id": req2["id"], "result": "resp2"}) + "\n")
        time.sleep(0.05)
        client_in.write(json.dumps({"id": req1["id"], "result": "resp1"}) + "\n")

    srv = threading.Thread(target=mock_server)
    srv.start()

    def do_req(name, timeout):
        results[name] = client.request(name, timeout=timeout)

    t1 = threading.Thread(target=do_req, args=("req1", 2.0))
    t2 = threading.Thread(target=do_req, args=("req2", 2.0))
    t1.start()
    t2.start()

    t1.join()
    t2.join()
    srv.join()

    assert results["req1"] == "resp1"
    assert results["req2"] == "resp2"
    client.close()


def test_rpc_notifications_handled():
    client_in = PipeIO()
    client_out = PipeIO()
    client = JsonRpcClient(stdin=client_out, stdout=client_in)

    received_notifications = []

    def handle_notify(method: str, params: dict):
        received_notifications.append((method, params))

    client.register_notification_handler(handle_notify)

    # Server sends notification (no id)
    client_in.write(json.dumps({"method": "session/update", "params": {"data": 123}}) + "\n")
    # Also send a valid response to an actual request
    def respond():
        req_line = client_out.readline()
        req = json.loads(req_line)
        client_in.write(json.dumps({"id": req["id"], "result": "done"}) + "\n")

    t = threading.Thread(target=respond)
    t.start()

    res = client.request("test", timeout=2.0)
    assert res == "done"
    t.join()

    # Verify notification was received and didn't interfere
    assert len(received_notifications) == 1
    assert received_notifications[0] == ("session/update", {"data": 123})
    client.close()


def test_rpc_malformed_json_ignored():
    client_in = PipeIO()
    client_out = PipeIO()
    client = JsonRpcClient(stdin=client_out, stdout=client_in)

    # Write corrupt data
    client_in.write("BROKEN_NON_JSON\n")
    client_in.write("{bad_json\n")

    def reply():
        req_line = client_out.readline()
        req = json.loads(req_line)
        client_in.write(json.dumps({"id": req["id"], "result": "recovered"}) + "\n")

    t = threading.Thread(target=reply)
    t.start()

    res = client.request("ping", timeout=2.0)
    assert res == "recovered"
    t.join()
    client.close()


def test_rpc_timeout():
    client_in = PipeIO()
    client_out = PipeIO()
    client = JsonRpcClient(stdin=client_out, stdout=client_in)

    with pytest.raises(JsonRpcTimeoutError, match="timed out after 0.1 seconds"):
        client.request("slow_method", timeout=0.1)

    client.close()


def test_rpc_error_raises():
    client_in = PipeIO()
    client_out = PipeIO()
    client = JsonRpcClient(stdin=client_out, stdout=client_in)

    def mock_server():
        req_line = client_out.readline()
        req = json.loads(req_line)
        client_in.write(json.dumps({"id": req["id"], "error": {"code": -32600, "message": "Invalid request"}}) + "\n")

    t = threading.Thread(target=mock_server)
    t.start()

    with pytest.raises(JsonRpcError, match="Invalid request"):
        client.request("bad_call", timeout=2.0)

    t.join()
    client.close()


def test_rpc_server_disconnect_unblocks():
    client_in = PipeIO()
    client_out = PipeIO()
    client = JsonRpcClient(stdin=client_out, stdout=client_in)

    def close_after_short_delay():
        time.sleep(0.05)
        client_in.close()

    t = threading.Thread(target=close_after_short_delay)
    t.start()

    with pytest.raises(JsonRpcError):
        client.request("will_be_disconnected", timeout=2.0)

    t.join()
    assert client.is_alive is False
    client.close()
