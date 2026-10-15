#!/usr/bin/env python3
"""Reconcile bounded size/original-audio profiles; never search or edit files."""
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

FORMAT = "Language: Not Original"
FAVORITE = "Favorites - UHD + Original"
SERVICES = {"sonarr": ("sonarr-tv", 8989), "radarr": ("radarr-movie", 7878)}
# MiB per minute. Lower minima support animation and efficient encodes.
SIZES = {"sonarr": {720: (2, 20, 35), 1080: (3, 40, 60), 2160: (10, 140, 200)},
         "radarr": {720: (2, 35, 50), 1080: (3, 70, 100), 2160: (10, 250, 350)}}


class API:
    def __init__(self, app):
        directory, port = SERVICES[app]
        config = ET.parse("/srv/swarm/" + directory + "/config/config.xml").getroot()
        self.key: str = config.findtext("ApiKey") or ""
        if not self.key:
            raise RuntimeError("Missing API key: " + app)
        self.base = "http://127.0.0.1:" + str(port) + (config.findtext("UrlBase") or "").rstrip("/") + "/api/v3/"

    def call(self, path, method="GET", body=None) -> Any:
        req = urllib.request.Request(self.base + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"X-Api-Key": self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            raise RuntimeError(method + " " + path + ": HTTP " + str(error.code)) from None
        except urllib.error.URLError:
            raise RuntimeError("API unavailable") from None


def spec_key(item):
    return {k: item.get(k) for k in ("name", "includeCustomFormatWhenRenaming")} | {
        "specifications": [{"name": s["name"], "implementation": s["implementation"],
            "negate": s.get("negate", False), "required": s.get("required", False),
            "fields": sorted([{"name": f["name"], "value": f.get("value")} for f in s["fields"]],
                             key=lambda f: f["name"])} for s in item["specifications"]]}


def desired_format():
    return {"name": FORMAT, "includeCustomFormatWhenRenaming": False, "specifications": [{
        "name": "Not Original Language", "implementation": "LanguageSpecification",
        "negate": True, "required": False,
        "fields": [{"name": "value", "value": -2}, {"name": "exceptLanguage", "value": False}]}]}


def configure_items(items, ceiling, favorite=False):
    result = copy.deepcopy(items)
    for item in result:
        if item.get("items"):
            item["items"] = configure_items(item["items"], ceiling, favorite)
            item["allowed"] = any(x["allowed"] for x in item["items"])
        else:
            quality = item.get("quality", {})
            name = quality.get("name", "").lower()
            banned = any(w in name for w in ("remux", "raw-hd", "br-disk", "dvd-r"))
            if favorite:
                item["allowed"] = quality.get("resolution") in (720, 1080, 2160) and not banned
            elif banned or quality.get("resolution", 0) > ceiling:
                item["allowed"] = False
    return result


def configure_profile(profile, cfid, language_ids, app, favorite=False):
    result = copy.deepcopy(profile)
    uhd = favorite or "uhd" in result["name"].lower() or "ultra" in result["name"].lower()
    result["items"] = configure_items(result["items"], 2160 if uhd else 1080, favorite)
    result["minFormatScore"] = 0
    result["formatItems"] = [copy.deepcopy(f) for f in result.get("formatItems", []) if f["format"] != cfid]
    for item in result["formatItems"]:
        if item["format"] in language_ids:
            item["score"] = 0
    result["formatItems"].append({"format": cfid, "name": FORMAT, "score": -10000})
    result["formatItems"].sort(key=lambda x: x["format"])
    if app == "radarr":
        result["language"] = {"id": -1, "name": "Any"}
    # Existing profiles keep upgrades disabled/current cutoffs unless no longer allowed.
    allowed_ids = [x.get("id", x.get("quality", {}).get("id")) for x in result["items"] if x["allowed"]]
    if not allowed_ids:
        raise RuntimeError("Profile has no allowed quality")
    if favorite or result.get("cutoff") not in allowed_ids:
        result["cutoff"] = allowed_ids[-1]
    if favorite:
        result["name"] = FAVORITE
        result["upgradeAllowed"] = True
        result["cutoffFormatScore"] = 0
        result["minUpgradeFormatScore"] = 1
    return result


def same_profile(a, b):
    def normalized(p):
        p = copy.deepcopy(p)
        p["formatItems"] = sorted(p.get("formatItems", []), key=lambda x: x["format"])
        return p
    return normalized(a) == normalized(b)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.apply == args.check:
        parser.error("Choose --apply or --check")
    # Collect and validate both services before writing anything.
    states = {}
    for app in SERVICES:
        api = API(app)
        state = {path: api.call(path) for path in
                 ("qualityprofile", "customformat", "qualitydefinition", "importlist")}
        schema = api.call("customformat/schema")
        language = next(x for x in schema if x["implementation"] == "LanguageSpecification")
        options = next(f["selectOptions"] for f in language["fields"] if f["name"] == "value")
        if not any(x["value"] == -2 and x["name"] == "Original" for x in options):
            raise RuntimeError("Original language unsupported")
        if len([x for x in state["customformat"] if x["name"] == FORMAT]) > 1:
            raise RuntimeError("Ambiguous custom format")
        states[app] = (api, state)
    if args.apply:
        directory = Path("/srv/swarm/media-policy/backups")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory.parent, 0o700)
        snapshot = directory / (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        with open(snapshot, "x", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            json.dump({a: s for a, (_, s) in states.items()}, stream)
    summaries = {}
    for app, (api, state) in states.items():
        changes = []
        existing = next((x for x in state["customformat"] if x["name"] == FORMAT), None)
        desired = desired_format()
        if existing is None:
            changes.append("create_original_language_format")
            cfid = api.call("customformat", "POST", desired)["id"] if args.apply else -999
        else:
            cfid = existing["id"]
            if spec_key(existing) != spec_key(desired):
                changes.append("update_original_language_format")
                if args.apply:
                    api.call("customformat/" + str(cfid), "PUT", desired | {"id": cfid})
        for definition in state["qualitydefinition"]:
            resolution = definition["quality"].get("resolution")
            if resolution not in SIZES[app]:
                continue
            minimum, preferred, maximum = SIZES[app][resolution]
            updated = definition | {"minSize": minimum, "preferredSize": preferred, "maxSize": maximum}
            if updated != definition:
                changes.append("size:" + definition["quality"]["name"])
                if args.apply:
                    api.call("qualitydefinition/" + str(definition["id"]), "PUT", updated)
        # Neutralize old language-specific boosts; original audio is the sole language rule.
        language_ids = {x["id"] for x in state["customformat"] if x["name"] in ("English", "Japanese", "Korean")}
        favorites = [x for x in state["qualityprofile"] if x["name"] == FAVORITE]
        if len(favorites) > 1:
            raise RuntimeError("Ambiguous favorite profile")
        for profile in state["qualityprofile"]:
            updated = configure_profile(profile, cfid, language_ids, app, profile["name"] == FAVORITE)
            if not same_profile(profile, updated):
                changes.append("profile:" + profile["name"])
                if args.apply:
                    api.call("qualityprofile/" + str(profile["id"]), "PUT", updated)
        if not favorites:
            base = next(p for p in state["qualityprofile"] if p["name"] == "Any")
            new = configure_profile(base, cfid, language_ids, app, True)
            new.pop("id", None)
            changes.append("create_favorite_profile")
            if args.apply:
                api.call("qualityprofile", "POST", new)
        # No list settings or title/file records may change in this reconciler.
        if args.apply and api.call("importlist") != state["importlist"]:
            raise RuntimeError("Import list changed during reconciliation")
        summaries[app] = {"changes": changes, "changed": bool(changes)}
    print(json.dumps({"changed": any(s["changed"] for s in summaries.values()), "services": summaries}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": type(error).__name__,
                          "detail": str(error) if isinstance(error, RuntimeError) else "Policy reconciliation failed"}))
        sys.exit(1)
