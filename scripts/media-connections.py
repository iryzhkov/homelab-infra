#!/usr/bin/env python3
"""Adopt existing media service connections through supported APIs."""
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ARR = {"sonarr": ("sonarr-tv", "sonarr", 8989, "v3"),
       "radarr": ("radarr-movie", "radarr", 7878, "v3"),
       "prowlarr": ("prowlarr", "prowlarr", 9696, "v1")}


def required(value):
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("A required connection credential or endpoint is missing")
    return value


def database_settings(path, table, column, value):
    # Identifiers are internal constants; values are parameterized.
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute("SELECT " + ("Content" if table == "GlobalSettings" else "Settings")
                          + " FROM " + table + " WHERE " + column + " = ?", (value,)).fetchall()
    if len(rows) != 1:
        raise RuntimeError("Missing or ambiguous protected credential record")
    return json.loads(rows[0][0])


def yaml_section(text, section):
    match = re.search(r"(?m)^" + re.escape(section) + r":\n((?:[ \t].*\n)*)", text)
    if not match:
        raise RuntimeError("Missing protected Bazarr configuration section")
    return match.group(1)


def yaml_key(text, section):
    match = re.search(r"""(?m)^\s+apikey:\s*['"]?([a-fA-F0-9]{32})['"]?\s*$""",
                      yaml_section(text, section))
    if not match:
        raise RuntimeError("Missing protected Bazarr API key")
    return match.group(1)


def fields_patch(item, values):
    result = copy.deepcopy(item)
    for name, value in values.items():
        matches = [f for f in result.get("fields", []) if f.get("name") == name]
        if len(matches) != 1:
            raise RuntimeError("Unsupported connection field schema")
        old = matches[0].get("value")
        # Root URL bases are represented as either null or empty by the arr apps.
        if name == "urlBase" and not old and not value:
            continue
        matches[0]["value"] = value
    return result


def unique(items, name, implementation):
    matches = [i for i in items if i.get("name") == name
               and i.get("implementation") == implementation]
    if len(matches) != 1:
        raise RuntimeError("Missing or ambiguous managed connection")
    return matches[0]


def nested_patch(item, prefix, values):
    result = copy.deepcopy(item)
    target = result
    for part in prefix:
        target = target[part]
    if not set(values).issubset(target):
        raise RuntimeError("Unsupported connection settings schema")
    for key, value in values.items():
        if key in ("subDir", "base_url") and not target[key] and not value:
            continue
        target[key] = value
    return result


def changed_paths(before, after, prefix=""):
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(set(before) | set(after)):
            result.extend(changed_paths(before.get(key), after.get(key),
                                        prefix + ("." if prefix else "") + str(key)))
        return result
    if before == after:
        return []
    # No values, including old values, belong in the public plan.
    return [prefix]


class Runtime:
    def __init__(self, root):
        self.root = Path(root)
        self.keys = {}
        self.bases = {}
        for name, (stack, _, port, version) in ARR.items():
            config = ET.parse(self.root / stack / "config/config.xml").getroot()
            self.keys[name] = required(config.findtext("ApiKey"))
            self.bases[name] = ("http://127.0.0.1:" + str(port)
                                + (config.findtext("UrlBase") or "").rstrip("/")
                                + "/api/" + version + "/")
        self.ombi_db = self.root / "ombi/config/OmbiSettings.db"
        ombi = database_settings(self.ombi_db, "GlobalSettings", "SettingsName", "OmbiSettings")
        self.keys["ombi"] = required(ombi.get("ApiKey"))
        self.bases["ombi"] = "http://127.0.0.1:3579" + (ombi.get("BaseUrl") or "").rstrip("/") + "/api/v1/"
        emby = database_settings(self.ombi_db, "GlobalSettings", "SettingsName", "EmbySettings")
        if emby.get("Enable") is not True or len(emby.get("Servers", [])) != 1:
            raise RuntimeError("Expected one enabled existing Ombi Emby server")
        self.keys["emby"] = required(emby["Servers"][0].get("ApiKey"))
        bazarr = (self.root / "bazarr/config/config/config.yaml").read_text()
        self.keys["bazarr"] = yaml_key(bazarr, "auth")
        base = re.search(r"(?m)^\s+base_url:\s*(.*)$", yaml_section(bazarr, "general"))
        self.bases["bazarr"] = ("http://127.0.0.1:6767"
                                + (base.group(1).strip().strip("'\"") if base else "").rstrip("/")
                                + "/api/")

    def call(self, app, path, method="GET", body=None, form=False) -> Any:
        data = None
        if body is not None:
            data = (urllib.parse.urlencode(body, doseq=True) if form else json.dumps(body)).encode()
        header = "ApiKey" if app == "ombi" else "X-API-KEY"
        request = urllib.request.Request(self.bases[app] + path, method=method, data=data,
                  headers={header: self.keys[app], "Content-Type":
                           "application/x-www-form-urlencoded" if form else "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            raise RuntimeError("Connection API returned HTTP " + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RuntimeError("Connection API is unavailable") from None

    def read(self, resource):
        app, path = resource["app"], resource["path"]
        value = self.call(app, path)
        if resource["kind"] == "fields":
            stack, dbname, _, _ = ARR[app]
            raw = database_settings(self.root / stack / ("config/" + dbname + ".db"),
                                    resource["table"], "Id", resource["id"])
            # Public arr APIs mask secrets. Hydrate only the credentials we own,
            # from a private read-only query, before comparisons and PUT.
            for field in value.get("fields", []):
                if field["name"] in resource["secret_fields"]:
                    field["value"] = raw.get(field["name"])
        return value

    def write(self, resource):
        app, path = resource["app"], resource["path"]
        if resource["kind"] == "bazarr":
            form = {}
            for section in ("sonarr", "radarr"):
                for key, value in resource["desired"][section].items():
                    if value != resource["before"][section].get(key):
                        form["settings-" + section + "-" + key] = str(value).lower() if isinstance(value, bool) else value
            self.call(app, path, "POST", form, form=True)
        else:
            result = self.call(app, path, "PUT" if resource["kind"] == "fields" else "POST",
                               resource["desired"])
            if app == "ombi" and result is not True:
                raise RuntimeError("Ombi rejected a connection update")

    def invariants(self):
        result = {}
        for app in ("sonarr", "radarr"):
            result[app] = {p: self.call(app, p) for p in ("importlist", "qualityprofile", "tag", "indexer")}
            # Hydrate indexer keys so they can be compared without public API masking.
            stack, dbname, _, _ = ARR[app]
            for indexer in result[app]["indexer"]:
                raw = database_settings(self.root / stack / ("config/" + dbname + ".db"),
                                        "Indexers", "Id", indexer["id"])
                for field in indexer.get("fields", []):
                    if field["name"] == "apiKey":
                        field["value"] = raw.get("apiKey")
        return result

    def force_sync(self):
        command = self.call("prowlarr", "command", "POST",
                            {"name": "ApplicationIndexerSync", "forceSync": True})
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status = self.call("prowlarr", "command/" + str(command["id"]))["status"]
            if status == "completed":
                return
            if status in ("failed", "aborted", "cancelled"):
                raise RuntimeError("Prowlarr connection synchronization failed")
            time.sleep(1)
        raise RuntimeError("Prowlarr connection synchronization timed out")


def plan(runtime, config):
    hostnames = {}
    for key in ("arr_host", "prowlarr_host", "emby_host", "qbit_host"):
        value = required(config.get(key))
        if any(c in value for c in "/?#@\r\n"):
            raise RuntimeError("Invalid inventory connection hostname")
        hostnames[key] = value
    user = required(config.get("qbit_username"))
    password = required(config.get("qbit_password"))
    urls = {"sonarr": "http://" + hostnames["arr_host"] + ":8989",
            "radarr": "http://" + hostnames["arr_host"] + ":7878",
            "prowlarr": "http://" + hostnames["prowlarr_host"] + ":9696"}
    resources = []
    for app in ("sonarr", "radarr"):
        item = unique(runtime.call(app, "downloadclient"), "qBittorrent", "QBittorrent")
        if item.get("enable") is not True:
            raise RuntimeError("Expected an enabled existing download client")
        r = {"name": app + "-qbittorrent", "app": app, "kind": "fields",
             "path": "downloadclient/" + str(item["id"]), "id": item["id"],
             "table": "DownloadClients", "secret_fields": ["username", "password"]}
        r["before"] = runtime.read(r)
        r["desired"] = fields_patch(r["before"], {"host": hostnames["qbit_host"], "port": 8080,
                       "useSsl": False, "urlBase": "", "username": user, "password": password})
        resources.append(r)

    applications = runtime.call("prowlarr", "applications")
    for app in ("sonarr", "radarr"):
        implementation = app.capitalize()
        item = unique(applications, implementation, implementation)
        if item.get("syncLevel") != "fullSync":
            raise RuntimeError("Expected the existing full-sync Prowlarr application")
        r = {"name": "prowlarr-" + app, "app": "prowlarr", "kind": "fields",
             "path": "applications/" + str(item["id"]), "id": item["id"],
             "table": "Applications", "secret_fields": ["apiKey"]}
        r["before"] = runtime.read(r)
        r["desired"] = fields_patch(r["before"], {"prowlarrUrl": urls["prowlarr"],
                       "baseUrl": urls[app], "apiKey": runtime.keys[app]})
        resources.append(r)

    r = {"name": "bazarr-arrs", "app": "bazarr", "kind": "bazarr", "path": "system/settings"}
    r["before"] = runtime.read(r)
    desired = copy.deepcopy(r["before"])
    for app, port in (("sonarr", 8989), ("radarr", 7878)):
        desired = nested_patch(desired, [app], {"ip": hostnames["arr_host"], "port": port,
                               "ssl": False, "base_url": "", "apikey": runtime.keys[app]})
    r["desired"] = desired
    resources.append(r)

    for app, port in (("sonarr", 8989), ("radarr", 7878), ("emby", 8096)):
        r = {"name": "ombi-" + app, "app": "ombi", "kind": "ombi", "path": "Settings/" + app}
        r["before"] = runtime.read(r)
        prefix = [] if app == "sonarr" else ["radarr"]
        if app == "emby":
            if r["before"].get("enable") is not True or len(r["before"].get("servers", [])) != 1:
                raise RuntimeError("Expected one enabled existing Emby integration")
            prefix = ["servers", 0]
        elif (r["before"] if app == "sonarr" else r["before"]["radarr"]).get("enabled") is not True:
            raise RuntimeError("Expected an enabled existing Ombi arr integration")
        r["desired"] = nested_patch(r["before"], prefix,
                       {"ip": hostnames["emby_host"] if app == "emby" else hostnames["arr_host"],
                        "port": port, "ssl": False, "subDir": "", "apiKey": runtime.keys[app]})
        resources.append(r)
    # Read the entire preservation baseline before the first mutation.
    return {"resources": resources, "invariants": runtime.invariants()}


def expected_after_sync(data, keys):
    """Permit only Prowlarr's managed remote URL/key propagation."""
    expected = copy.deepcopy(data["invariants"])
    for app in ("sonarr", "radarr"):
        resource = next(r for r in data["resources"] if r["name"] == "prowlarr-" + app)
        old = next(f["value"] for f in resource["before"]["fields"] if f["name"] == "prowlarrUrl").rstrip("/")
        new = next(f["value"] for f in resource["desired"]["fields"] if f["name"] == "prowlarrUrl").rstrip("/")
        for indexer in expected[app].get("indexer", []):
            fields = {f["name"]: f for f in indexer.get("fields", [])}
            address = fields.get("baseUrl", {}).get("value", "")
            if not isinstance(address, str) or not (address == old or address.startswith(old + "/")):
                continue
            fields["baseUrl"]["value"] = new + address[len(old):]
            if "apiKey" in fields:
                fields["apiKey"]["value"] = keys["prowlarr"]
    return expected


def save_snapshot(data, backup_root):
    directory = Path(backup_root)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".json")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return str(path)


def reconcile(runtime, config, apply=False, backup_root="/var/backups/media-connections"):
    data = plan(runtime, config)
    changes = [r for r in data["resources"] if r["before"] != r["desired"]]
    result = {"changed": bool(changes), "mode": "apply" if apply else "check",
              "connections": [{"name": r["name"], "fields": changed_paths(r["before"], r["desired"])}
                              for r in changes], "managed_connections": len(data["resources"])}
    if not apply or not changes:
        return result
    for resource in data["resources"]:
        if runtime.read(resource) != resource["before"]:
            raise RuntimeError("Connection state changed during preflight; rerun")
    if runtime.invariants() != data["invariants"]:
        raise RuntimeError("Preserved application settings changed during preflight; rerun")
    result["backup"] = save_snapshot(data, backup_root)
    for resource in changes:
        if runtime.read(resource) != resource["before"]:
            raise RuntimeError("Connection state changed before update; inspect protected snapshot")
        runtime.write(resource)
        if runtime.read(resource) != resource["desired"]:
            raise RuntimeError("Connection readback mismatch; inspect protected snapshot")
    expected = data["invariants"]
    if any(r["app"] == "prowlarr" for r in changes):
        expected = expected_after_sync(data, runtime.keys)
        runtime.force_sync()
    for resource in data["resources"]:
        if runtime.read(resource) != resource["desired"]:
            raise RuntimeError("Final connection verification failed; inspect protected snapshot")
    # Full sync may propagate the connection URL/key, but no categories/tags/policy.
    if runtime.invariants() != expected:
        raise RuntimeError("Non-owned settings changed; inspect protected snapshot before continuing")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-root", default="/srv/swarm")
    parser.add_argument("--backup-root", default="/var/backups/media-connections")
    args = parser.parse_args()
    info = Path(args.config).stat()
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError("Connection input must be private to its owner")
    if args.apply and os.geteuid() != 0:
        raise RuntimeError("Apply requires root to protect rollback snapshots")
    config = json.loads(Path(args.config).read_text())
    print(json.dumps(reconcile(Runtime(args.state_root), config, args.apply, args.backup_root)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": str(error) if isinstance(error, RuntimeError)
                          else "Connection reconciliation failed: " + type(error).__name__}), file=sys.stderr)
        sys.exit(1)
