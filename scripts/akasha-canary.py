#!/usr/bin/env python3
"""Operator-run loopback canary; read a short-lived bearer from a private file."""
import argparse
import json
import os
import stat
import time
import urllib.error
import urllib.request

def run(token, key, port=1935):
    url = "http://127.0.0.1:%d" % port
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def request(path, payload=None, bearer=token):
        headers = {"Authorization": "Bearer " + bearer, "Idempotency-Key": key}
        data = None if payload is None else json.dumps(payload, sort_keys=True).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            with opener.open(urllib.request.Request(url + path, data=data, headers=headers), timeout=10) as r:
                return r.status, json.loads(r.read(8193))
        except urllib.error.HTTPError as e:
            # Do not log request headers, tokens or arbitrary server error prose.
            return e.code, {}
    payload = {"v": 1, "kind": "report", "source": "chaldea.daily-health.agent-a",
               "host": "agent-a", "title": "Trust rollout canary", "severity": "info",
               "data": {"status": "ok", "checks": [{"name": "rollout", "status": "ok"}]}}
    event = None
    for _ in range(10):
        code, receipt = request("/api/v1/events", payload)
        if code == 201 and receipt.get("state") == "visible":
            event = receipt.get("event_id")
            break
        if code not in (202, 503):
            raise RuntimeError("append did not return a visible or pending receipt")
        time.sleep(1)
    if not event:
        raise RuntimeError("no visible durable receipt within canary bound")
    code, same = request("/api/v1/events", payload)
    if code != 201 or same.get("event_id") != event:
        raise RuntimeError("idempotent retry changed the receipt")
    code, _ = request("/api/v1/events", {**payload, "title": "Changed rollout canary"})
    if code != 409:
        raise RuntimeError("changed-payload retry did not conflict")
    code, _ = request("/api/v1/events")
    if code != 403:
        raise RuntimeError("report token could read")
    code, _ = request("/api/v1/events", {**payload, "source": "agent-a.fleet"})
    if code != 403:
        raise RuntimeError("foreign source accepted")
    code, _ = request("/api/v1/events", {**payload, "kind": "feed_post", "data": {}})
    if code != 403:
        raise RuntimeError("foreign kind accepted")
    a,b,c = token.split(".")
    tampered = a + "." + b + "." + ("A" if c[0] != "A" else "B") + c[1:]
    code, _ = request("/api/v1/events", payload, tampered)
    if code not in (401,403):
        raise RuntimeError("tampered signature accepted")
    print(json.dumps({"ok": True, "event_id": event, "checks": 7}))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--token-file", required=True)
    p.add_argument("--key", required=True)
    args = p.parse_args()
    fd = os.open(args.token_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        s = os.fstat(fd)
        if not (stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and
                s.st_uid in (0, os.geteuid()) and stat.S_IMODE(s.st_mode) == 0o600 and s.st_size <= 16385):
            raise RuntimeError("unsafe canary token file")
        token = os.read(fd,16386).decode().strip()
    finally:
        os.close(fd)
    run(token,args.key)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("canary failed; inspect authorization, trust, clock and durable receipt health")
