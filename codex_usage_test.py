
import json
import subprocess
import sys
import shutil
import os
from datetime import datetime

process = None


def send(message):
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()


def receive(request_id):
    while True:
        line = process.stdout.readline()

        if not line:
            raise RuntimeError(
                "Codex App Server closed its output."
            )

        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(message["error"])

            return message.get("result", {})


try:
    print("Locating Codex CLI...")

    codex_path = shutil.which("codex")

    if not codex_path:
        raise RuntimeError("Codex CLI not found.")

    print("Found:", codex_path)
    print("Starting Codex App Server...")

    process = subprocess.Popen(
        f'"{codex_path}" app-server',
        shell=True,
        executable=os.environ.get("COMSPEC", "cmd.exe"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        encoding="utf-8",
        bufsize=1
    )

    print("Initializing protocol...")

    send({
        "method": "initialize",
        "id": 0,
        "params": {
            "clientInfo": {
                "name": "coderelay_usage_test",
                "title": "CodeRelay Usage Test",
                "version": "0.1.0"
            }
        }
    })

    initialization = receive(0)

    send({
        "method": "initialized",
        "params": {}
    })

    print("\nChecking account...")

    send({
        "method": "account/read",
        "id": 1,
        "params": {"refreshToken": False}
    })

    account = receive(1)

    print(json.dumps(account, indent=2))

    print("\nFetching rate limits...")

    send({
        "method": "account/rateLimits/read",
        "id": 2,
        "params": {}
    })

    result = receive(2)

    print("\nRAW RATE LIMIT RESPONSE:")
    print(json.dumps(result, indent=2))

    limits = result.get("rateLimitsByLimitId") or {}

    if not limits and result.get("rateLimits"):
        single = result["rateLimits"]
        limits = {single.get("limitId", "default"): single}

    if limits:
        print("\nUSAGE SUMMARY:")

        for name, bucket in limits.items():
            print(f"\nBucket: {name}")

            for window in ("primary", "secondary"):
                data = bucket.get(window)

                if not data:
                    continue

                used = data.get("usedPercent")
                reset = data.get("resetsAt")

                if used is not None:
                    print(f"{window}: {used}% used")
                    print(f"Remaining: {100 - used}%")

                if reset:
                    print(
                        "Resets at:",
                        datetime.fromtimestamp(reset).astimezone()
                    )

            print("Limit reached:", bucket.get("rateLimitReachedType"))

    else:
        print("\nNo rate-limit buckets returned.")

except Exception as error:
    print("\nERROR:", error)
    sys.exit(1)

finally:
    if process and process.poll() is None:
        process.terminate()
        process.wait(timeout=5)

    print("\nTest finished.")
