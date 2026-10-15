#!/usr/bin/env python3
"""Bootstrap Jellystat without exposing credentials; historical sync is explicit."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class BootstrapError(Exception):
    pass


def request(base, route, payload=None, token=None, timeout=30):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if payload is None else json.dumps(payload).encode()
    try:
        with urlopen(Request(base + route, data=data, headers=headers), timeout=timeout) as response:
            body = response.read().decode()
    except HTTPError as error:
        raise BootstrapError("Jellystat " + route + " returned HTTP " + str(error.code)) from None
    except (URLError, TimeoutError):
        raise BootstrapError("Jellystat request failed: " + route) from None
    try:
        return json.loads(body)
    except ValueError:
        return body


def password_digest():
    result = subprocess.run(
        ["docker", "ps", "--filter", "label=com.docker.swarm.service.name=jellystat_jellystat",
         "--format", "{{.ID}}"], check=True, capture_output=True, text=True)
    containers = result.stdout.split()
    if len(containers) != 1:
        raise BootstrapError("Expected exactly one running Jellystat app container")
    result = subprocess.run(
        ["docker", "exec", containers[0], "node", "-p",
         "require('crypto-js').SHA3(require('fs').readFileSync('/run/secrets/js_password','utf8').trim()).toString()"],
        check=True, capture_output=True, text=True)
    digest = result.stdout.strip()
    if len(digest) != 128 or any(c not in "0123456789abcdef" for c in digest):
        raise BootstrapError("Invalid password digest")
    return digest


def emby_settings(path):
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as database:
        database.execute("PRAGMA query_only=ON")
        rows = database.execute(
            "SELECT Content FROM GlobalSettings WHERE SettingsName='EmbySettings'").fetchall()
    if len(rows) != 1:
        raise BootstrapError("Expected one Ombi Emby settings record")
    servers = json.loads(rows[0][0]).get("Servers", [])
    if len(servers) != 1 or not servers[0].get("ApiKey"):
        raise BootstrapError("Expected one configured Ombi Emby server")
    server = servers[0]
    host = server["Ip"]
    if host in ("localhost", "127.0.0.1", "::1") or "://" in host or "/" in host:
        raise BootstrapError("Ombi Emby address must be reachable from the Jellystat container")
    subdir = (server.get("SubDir") or "emby").strip("/")
    url = "{}://{}:{}/{}".format(
        "https" if server.get("Ssl") else "http", host, int(server["Port"]), subdir)
    return {"JF_HOST": url.rstrip("/"), "JF_API_KEY": server["ApiKey"]}


def save_key(path, key):
    path = Path(path)
    if not path.parent.is_dir():
        raise BootstrapError("Jellystat configuration directory does not exist")
    stat = path.parent.stat()
    if stat.st_uid != 0 or stat.st_mode & 0o077:
        raise BootstrapError("Jellystat configuration directory must be root-owned mode 0700")
    if path.exists() and path.read_text() == key and path.stat().st_uid == 1000 and path.stat().st_mode & 0o777 == 0o400:
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=".janitorr-key-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(key)
            output.flush()
            os.fsync(output.fileno())
            os.fchown(output.fileno(), 1000, 1000)
            os.fchmod(output.fileno(), 0o400)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--import-history", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:3012")
    parser.add_argument("--ombi-db", default="/srv/swarm/ombi/config/OmbiSettings.db")
    parser.add_argument("--key-file", default="/srv/swarm/jellystat/janitorr-api-key")
    args = parser.parse_args()
    if args.check and (args.sync or args.import_history):
        parser.error("Historical sync requires --apply")
    config = request(args.base_url, "/auth/isConfigured")
    state = config.get("state")
    if state not in (0, 1, 2):
        raise BootstrapError("Unrecognized Jellystat configuration state")
    summary = {"changed": False, "version": config.get("version"), "configured": state == 2}
    if args.check:
        summary["key_file_present"] = Path(args.key_file).is_file()
        print(json.dumps(summary))
        return
    credentials = {"username": "igor", "password": password_digest()}
    if state == 0:
        request(args.base_url, "/auth/createuser", credentials)
        summary["changed"] = True
    if state < 2:
        request(args.base_url, "/auth/configSetup", emby_settings(args.ombi_db))
        summary["changed"] = True
    token = request(args.base_url, "/auth/login", credentials)["token"]
    keys = request(args.base_url, "/api/keys", token=token)
    matches = [entry for entry in keys if entry.get("name") == "Janitorr"]
    if len(matches) > 1:
        raise BootstrapError("Multiple Janitorr API keys exist")
    if not matches:
        keys = request(args.base_url, "/api/keys", {"name": "Janitorr"}, token)
        matches = [entry for entry in keys if entry.get("name") == "Janitorr"]
        summary["changed"] = True
    if len(matches) != 1 or not matches[0].get("key"):
        raise BootstrapError("Janitorr API key was not returned")
    summary["changed"] = save_key(args.key_file, matches[0]["key"]) or summary["changed"]
    summary["configured"] = request(args.base_url, "/auth/isConfigured")["state"] == 2
    if args.sync:
        request(args.base_url, "/sync/beginSync", token=token, timeout=600)
        summary.update(changed=True, library_sync_requested=True)
    if args.import_history:
        request(args.base_url, "/sync/syncPlaybackPluginData", token=token, timeout=600)
        summary.update(changed=True, history_import_requested=True)
    # Task HTTP success does not prove import success: inspect logs and DB counts.
    print(json.dumps(summary))


if __name__ == "__main__":
    try:
        main()
    except (BootstrapError, OSError, sqlite3.Error, ValueError, KeyError, subprocess.SubprocessError) as error:
        # Never echo raw API bodies, subprocess output, DB records or credentials.
        message = str(error) if isinstance(error, BootstrapError) else type(error).__name__
        print(json.dumps({"failed": True, "error": message}))
        raise SystemExit(1)
