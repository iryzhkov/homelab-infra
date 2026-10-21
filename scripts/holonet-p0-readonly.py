#!/usr/bin/env python3
"""One-shot, fixed-GET Emby P0 inventory. Credentials never leave this process."""
import argparse
import contextlib
import signal
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = "http://127.0.0.1:8096/emby/"
SERVER = "3c86e6d7ff6c4a3296522cdf03d22bb0"
DEADLINE = "2026-10-20T22:52:34+00:00"
MAX_REQUESTS = 200
REQUEST_SECONDS = 15
MAX_RESPONSE = 2 * 1024 * 1024
MAX_BYTES = 16 * 1024 * 1024
LIBRARY_NAMES = {"Movies", "Anime", "TV", "TV Shows"}
HOME_NAMES = {"Movie Night", "Continue Watching", "Browse Libraries", "Explore Anime", "Your Favorites", "Movie Collections"}
READ_PATH = re.compile(r"(?:System/Info(?:/Public)?|Plugins|Library/VirtualFolders|user_usage_stats/HourlyReport|Users|Users/Public|Users/[0-9a-f]{32}(?:/Views|/HomeSections|/Sections/[A-Za-z0-9_-]{1,128}/Items|/Items(?:/Resume|/[A-Za-z0-9_-]{1,128})?)?|Playlists/[A-Za-z0-9_-]{1,128}/Items)")
QUERY_KEYS = {"IncludeHidden", "AllowDynamicChildren", "isEditMode", "ParentId", "Recursive", "IncludeItemTypes", "Fields", "EnableUserData", "StartIndex", "Limit", "SortBy", "SortOrder", "EnableTotalRecordCount", "IsFavorite", "UserId", "user_id", "days", "end_date", "filter"}
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class Refused(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused("redirect_refused")


def opaque(value):
    text = str(value)
    if not SAFE_ID.fullmatch(text) or type(value) is bool:
        raise Refused("invalid_backend_identifier")
    return text


def id_shape(value):
    text = str(value)
    if type(value) is int:
        return "integer"
    if re.fullmatch(r"[0-9a-f]{32}", text):
        return "hex32-string"
    if text.isdecimal():
        return "decimal-string"
    return "opaque-string"


def items(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("Items"), list):
        raise Refused("invalid_items_shape")
    return payload["Items"]


def summarize(payload, enhanced_userdata=False):
    rows = items(payload)
    if any(not isinstance(row, dict) for row in rows):
        raise Refused("invalid_item_shape")
    user_data = [row["UserData"] for row in rows if isinstance(row.get("UserData"), dict)]
    counts = {}
    for row in rows:
        kind = row.get("Type")
        kind = kind if kind in {"Movie", "Series", "Episode", "Playlist", "BoxSet", "CollectionFolder", "Folder"} else "other"
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "returned": len(rows), "reported_total": payload.get("TotalRecordCount") if type(payload.get("TotalRecordCount")) is int else None,
        "types": counts, "id_shapes": sorted({id_shape(row.get("Id")) for row in rows}),
        "userdata_records": len(user_data),
        "userdata_field_presence": {k: sum(k in d for d in user_data) for k in ("Played", "PlayCount", "LastPlayedDate", "PlaybackPositionTicks", "IsFavorite")},
        "played_true": sum(d.get("Played") is True for d in user_data),
        "playcount_positive": sum(type(d.get("PlayCount")) is int and d["PlayCount"] > 0 for d in user_data) if enhanced_userdata else None,
        "lastplayed_present": sum(bool(d.get("LastPlayedDate")) for d in user_data) if enhanced_userdata else None,
        "enhanced_userdata_qualified": enhanced_userdata,
        "resume_positive": sum(type(d.get("PlaybackPositionTicks")) is int and d["PlaybackPositionTicks"] > 0 for d in user_data),
        "favorite_true": sum(d.get("IsFavorite") is True for d in user_data),
        "provider_ids_present": sum(isinstance(r.get("ProviderIds"), dict) and bool(r["ProviderIds"]) for r in rows),
        "playlist_entry_ids_present": sum(bool(r.get("PlaylistItemId")) for r in rows),
    }


@contextlib.contextmanager
def absolute_read_deadline(deadline):
    # This one-shot Linux operator tool runs on the main thread. Do not replace
    # an existing process timer or claim socket inactivity as elapsed time.
    if any(signal.getitimer(signal.ITIMER_REAL)):
        raise Refused("existing_process_timer")
    remaining = min(REQUEST_SECONDS, deadline - time.time())
    if remaining <= 0:
        raise Refused("preflight_budget_exhausted")
    def expired(signum, frame):
        raise Refused("absolute_read_deadline")
    previous = signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, remaining)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class Reader:
    def __init__(self, token=None, opener=None, deadline=DEADLINE, prior_requests=0):
        self.token = token
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.deadline = dt.datetime.fromisoformat(deadline).timestamp()
        if type(prior_requests) is not int or not 0 <= prior_requests < MAX_REQUESTS:
            raise Refused("invalid_prior_request_count")
        self.prior_requests = prior_requests
        self.requests = 0
        self.bytes = 0
        self.ledger = []

    def get(self, path, label, query=None, authenticated=True):
        query = query or {}
        if not READ_PATH.fullmatch(path) or not set(query).issubset(QUERY_KEYS):
            raise Refused("route_not_allowlisted")
        if path == "user_usage_stats/HourlyReport" and (set(query) != {"user_id", "days", "end_date", "filter"} or not re.fullmatch(r"[0-9a-f]{32}", str(query["user_id"])) or query["days"] != 7 or query["end_date"] != "2026-10-20" or query["filter"] != "Movie,Episode"):
            raise Refused("history_scope_refused")
        if "Limit" in query and (type(query["Limit"]) is not int or not 1 <= query["Limit"] <= 25):
            raise Refused("page_limit_refused")
        if "StartIndex" in query and query["StartIndex"] not in (0, 25):
            raise Refused("page_offset_refused")
        if time.time() >= self.deadline or self.requests + self.prior_requests >= MAX_REQUESTS or self.bytes >= MAX_BYTES:
            raise Refused("preflight_budget_exhausted")
        if authenticated and not self.token:
            raise Refused("credential_missing")
        suffix = "?" + urllib.parse.urlencode(query) if query else ""
        headers = {"Accept": "application/json"}
        if authenticated:
            assert self.token is not None
            headers["X-Emby-Token"] = self.token
        request = urllib.request.Request(BASE + path + suffix, headers=headers, method="GET")
        self.requests += 1
        started = time.monotonic()
        status = None
        size = 0
        try:
            with absolute_read_deadline(self.deadline):
                with self.opener.open(request, timeout=min(REQUEST_SECONDS, max(0.001, self.deadline-time.time()))) as response:
                    status = response.status
                    allowance = min(MAX_RESPONSE, MAX_BYTES - self.bytes)
                    content = response.read(allowance)
                    size = len(content)
                    self.bytes += size
                    # Conservatively refuse a body that fills the remaining
                    # allowance; never consume an extra byte to probe for EOF.
                    if size >= allowance:
                        raise Refused("response_budget_exhausted")
                    if status != 200:
                        return None
                    return json.loads(content)
        except urllib.error.HTTPError as error:
            status = error.code
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            raise Refused("backend_read_failed") from None
        finally:
            self.ledger.append({"label": label, "method": "GET", "status": status,
                                "bytes": size, "elapsed_ms": round((time.monotonic()-started)*1000)})


def credential():
    # Same protected current source as the existing Emby operator helper.
    # Do not import its clients() function, which also opens unrelated arr keys.
    with sqlite3.connect("file:/srv/swarm/ombi/config/OmbiSettings.db?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        records = db.execute("SELECT Content FROM GlobalSettings WHERE SettingsName = ?", ("EmbySettings",)).fetchall()
    if len(records) != 1:
        raise Refused("credential_source_ambiguous")
    settings = json.loads(records[0][0])
    servers = settings.get("Servers", [])
    if settings.get("Enable") is False or len(servers) != 1 or servers[0].get("Enable") is False:
        raise Refused("credential_source_disabled")
    key = servers[0].get("ApiKey")
    if not isinstance(key, str) or not 1 <= len(key) <= 512 or any(c.isspace() for c in key):
        raise Refused("credential_source_invalid")
    return key


def probe(reader, report=None):
    if report is None:
        report = {}
    report.update({"profiles": {}, "limitations": [
        "API evidence is not authenticated web or physical-TV rendering proof",
        "item UserData is a bounded state sample, not a complete viewing-session ledger",
        "server API key does not prove minimum per-user credential reach",
        "no denied profile, credential issuance, mutation or POST read was attempted"]})
    info = reader.get("System/Info/Public", "server-public", authenticated=False)
    if not isinstance(info, dict) or info.get("Id") != SERVER:
        raise Refused("server_identity_mismatch")
    report["server_version"] = str(info.get("Version", ""))[:32]
    authenticated = reader.get("System/Info", "server-authenticated")
    report["authenticated_info_access"] = authenticated is not None
    plugins = reader.get("Plugins", "installed-plugins")
    report["plugins"] = [{"name": p["Name"], "version": str(p.get("Version", ""))[:32]}
                         for p in plugins or [] if isinstance(p, dict) and isinstance(p.get("Name"), str)
                         and re.fullmatch(r"[A-Za-z0-9 ._()+-]{1,80}", p["Name"])]
    users = reader.get("Users/Public", "select-authorized-profiles", authenticated=False)
    if not isinstance(users, list):
        raise Refused("users_unavailable")
    for name in ("Igor", "TV"):
        selected = [u for u in users if u.get("Name") == name]
        if not selected:
            # Existing operator helper uses this exact GET to resolve hidden
            # profiles. Retain only the approved exact-name match in memory.
            private_users = reader.get("Users", name + "-hidden-profile-lookup")
            if not isinstance(private_users, list):
                raise Refused("hidden_profile_lookup_unavailable")
            selected = [u for u in private_users if u.get("Name") == name]
            private_users = None
        if len(selected) != 1 or not re.fullmatch(r"[0-9a-f]{32}", str(selected[0].get("Id"))):
            raise Refused("authorized_profile_ambiguous")
        uid = selected[0]["Id"]
        prefix = "Users/" + uid
        details = reader.get(prefix, name + "-profile-policy-flags")
        if not isinstance(details, dict) or details.get("Name") != name:
            raise Refused("authorized_profile_identity_mismatch")
        policy = details.get("Policy") or {}
        profile: dict[str, Any] = {"policy_flags": {k: policy[k] for k in ("IsAdministrator", "EnableAllFolders", "EnableContentDeletion", "EnablePublicSharing", "EnableContentDownloading") if type(policy.get(k)) is bool}}
        report["profiles"][name] = profile
        views_payload = reader.get(prefix + "/Views", name + "-visible-libraries", {"IncludeHidden": "false", "AllowDynamicChildren": "false"})
        views = items(views_payload)
        visible = [v for v in views if v.get("Type") == "CollectionFolder"]
        profile["visible_view_count"] = len(views)
        profile["sampled_libraries"] = []
        sections = reader.get(prefix + "/HomeSections", name + "-home-sections", {"isEditMode": "true"})
        if not isinstance(sections, list) or len(sections) > 30:
            raise Refused("home_sections_shape_or_budget")
        profile["home_rows"] = []
        for index, section in enumerate(sections):
            sid = opaque(section.get("Id"))
            title = section.get("CustomName") or section.get("Name")
            query = section.get("Query") or {}
            row = {"id": section["Id"], "id_shape": id_shape(section["Id"]),
                   "name": title if title in HOME_NAMES else "other",
                   "section_type": section.get("SectionType") if section.get("SectionType") in {"items", "resume", "userviews", "collections", "playlists"} else "other",
                   "query_keys": sorted(k for k in query if k in {"IsPlayed", "IsFavorite", "ParentId", "Ids", "IncludeItemTypes", "Tags", "Genres"}),
                   "library_exclusion_count": len(section.get("ExcludedFolders") or []),
                   "content_sha256": hashlib.sha256(json.dumps(section, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
            sample = reader.get(prefix + "/Sections/" + sid + "/Items", name + "-row-" + str(index) + "-items", {"Limit": 5})
            row["sample"] = summarize(sample) if sample is not None else None
            profile["home_rows"].append(row)
        for view_index, view in enumerate(visible[:3]):
            library_name = view["Name"] if view.get("Name") in LIBRARY_NAMES else "other-view-" + str(view_index)
            library = {"name": library_name, "id_shape": id_shape(view.get("Id")), "pages": []}
            profile["sampled_libraries"].append(library)
            parent = opaque(view["Id"])
            previous = set()
            for offset in (0, 25):
                query = {"ParentId": parent, "Recursive": "true", "IncludeItemTypes": "Movie,Episode",
                         "Fields": "ProviderIds,UserDataPlayCount,UserDataLastPlayedDate", "EnableUserData": "true", "StartIndex": offset,
                         "Limit": 25, "SortBy": "SortName", "SortOrder": "Ascending", "EnableTotalRecordCount": "true"}
                payload = reader.get(prefix + "/Items", name + "-" + library_name + "-page-" + str(offset//25), query)
                if payload is None:
                    library["pages"].append({"unavailable": True})
                    break
                summary = summarize(payload, enhanced_userdata=True)
                ids = {str(r["Id"]) for r in items(payload)}
                summary["overlap_previous_page"] = len(ids & previous)
                previous = ids
                library["pages"].append(summary)
                if len(items(payload)) < 25:
                    break
        objects = reader.get(prefix + "/Items", name + "-curation-object-sample",
                             {"Recursive": "true", "IncludeItemTypes": "Playlist,BoxSet", "EnableUserData": "false", "Limit": 10, "StartIndex": 0})
        profile["curation_objects"] = summarize(objects) if objects is not None else None
    return report


def history_report(reader, uid, name):
    """One reviewed read-only plugin aggregate, scoped to one approved user."""
    if name not in ("Igor", "TV"):
        raise Refused("history_profile_refused")
    payload = reader.get("user_usage_stats/HourlyReport", name + "-history-hourly-7days",
                         {"user_id": uid, "days": 7, "end_date": "2026-10-20", "filter": "Movie,Episode"})
    if payload is None:
        return {"available": False}
    if not isinstance(payload, dict) or len(payload) > 168 or any(not re.fullmatch(r"[0-6]-[0-2][0-9]", k) or type(v) not in (int, float) for k, v in payload.items()):
        raise Refused("history_report_shape")
    return {"available": True, "days": 7, "end_day": "2026-10-20", "buckets": len(payload),
            "positive_buckets": sum(v > 0 for v in payload.values()),
            "units": "unqualified", "coverage": "reported activity; collection freshness/completeness unqualified"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-approved-readonly", action="store_true")
    parser.add_argument("--prior-requests", type=int, default=0, help="Consumed requests in the approved window")
    args = parser.parse_args()
    if not args.execute_approved_readonly or os.geteuid() != 0 or socket.gethostname() != "homelab":
        raise SystemExit("Refused: approved homelab operator invocation required")
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    reader = Reader(prior_requests=args.prior_requests)
    result: dict[str, Any] = {}
    try:
        # Check fixed server identity before opening any credential.
        public = reader.get("System/Info/Public", "pre-credential-identity", authenticated=False)
        if not isinstance(public, dict) or public.get("Id") != SERVER:
            raise Refused("server_identity_mismatch")
        reader.token = credential()
        probe(reader, result)
        result["outcome"] = "completed"
    except Exception as error:
        result.update({"outcome": "refused", "reason": str(error) if isinstance(error, Refused) else "private_operation_failed"})
    finally:
        reader.token = None
    result.update({"started": started, "ended": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "request_count": reader.requests, "response_bytes": reader.bytes, "ledger": reader.ledger,
                   "deadline": DEADLINE, "request_cap": MAX_REQUESTS, "prior_requests": reader.prior_requests,
                   "cumulative_requests": reader.prior_requests + reader.requests})
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
