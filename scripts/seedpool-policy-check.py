#!/usr/bin/env python3
"""Read-only live seed-pool/retirement check; never stops or deletes torrents."""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request
from seedpool_policy import snapshot, retirement_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/media-observability/qbit.json")
    parser.add_argument("--retire-file", help="Private JSON array of torrent hashes; evaluates only")
    args = parser.parse_args()
    path = Path(args.config)
    stat = path.stat()
    if stat.st_uid != 0 or stat.st_mode & 0o077:
        raise RuntimeError("qBittorrent credential file must be root-owned and private")
    config = json.loads(path.read_text())
    url = config["url"].rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("Unsupported qBittorrent address")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    login = urllib.parse.urlencode({"username": config["username"], "password": config["password"]}).encode()
    with opener.open(urllib.request.Request(url + "/api/v2/auth/login", data=login), timeout=20) as response:
        if response.read().strip() != b"Ok.":
            raise RuntimeError("qBittorrent authentication failed")
    with opener.open(url + "/api/v2/torrents/info", timeout=30) as response:
        rows = json.load(response)
    result = snapshot(rows)
    if args.retire_file:
        decision = retirement_plan(rows, json.loads(Path(args.retire_file).read_text()))
        result["retirement_plan"] = decision
    print(json.dumps(result, sort_keys=True))
    return 2 if args.retire_file and not result["retirement_plan"]["allowed"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # Never echo response bodies, tracker URLs, credentials or request objects.
        print(json.dumps({"error": str(error) if isinstance(error, RuntimeError) else type(error).__name__}), file=sys.stderr)
        sys.exit(1)
