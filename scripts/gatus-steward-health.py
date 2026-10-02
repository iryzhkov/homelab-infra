#!/usr/bin/env python3
"""Read-only HTTP health checks backed by the steward coordinator's worker list."""

import argparse
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def worker_statuses(steward):
    result = subprocess.run(
        [steward, "worker", "list", "--json"],
        capture_output=True,
        check=True,
        timeout=8,
        text=True,
    )
    payload = json.loads(result.stdout)
    workers = payload["workers"]
    return {
        item["requirement"]["workerId"]: (
            item["health"] == "ready"
            and item["stale"] is False
            and item["snapshot"]["connected"] is True
        )
        for item in workers
    }


def handler_for(steward):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health/coordinator" and not self.path.startswith("/health/worker/"):
                self.send_error(404)
                return
            try:
                statuses = worker_statuses(steward)
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
                self.send_response(503)
                self.end_headers()
                return
            if self.path == "/health/coordinator":
                status = 200
            else:
                worker_id = self.path.removeprefix("/health/worker/")
                status = 200 if statuses.get(worker_id) else 503
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(("ok\n" if status == 200 else "unavailable\n").encode())

    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    parser.add_argument("--steward", required=True)
    args = parser.parse_args()
    ThreadingHTTPServer((args.bind, 3003), handler_for(args.steward)).serve_forever()
