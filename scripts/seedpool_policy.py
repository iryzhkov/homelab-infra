#!/usr/bin/env python3
"""Read-only SeedPool retention accounting. This module has no mutation API."""
from collections import Counter
import math
import re
from urllib.parse import urlsplit

FLOOR_BYTES = 20 * 1024**4
MINIMUM_SEED_SECONDS = 10 * 24 * 60 * 60
ACTIVE_STATES = frozenset(("uploading", "stalledUP", "forcedUP"))
HASH = re.compile(r"^[0-9a-fA-F]{40}([0-9a-fA-F]{24})?$")


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def tracker_kind(torrent):
    try:
        tracker = torrent.get("tracker", "")
        host = (urlsplit(tracker).hostname or "").lower() if isinstance(tracker, str) else ""
    except ValueError:
        host = ""
    if not host:
        return "unknown"
    return "seedpool" if host == "seedpool.org" or host.endswith(".seedpool.org") else "other"


def classify(torrent):
    kind = tracker_kind(torrent)
    if kind != "seedpool":
        return kind, 0
    required = ("total_size", "size", "progress", "amount_left", "seeding_time")
    if any(not number(torrent.get(key)) for key in required):
        return "unknown", 0
    if torrent["progress"] > 1 or any(torrent[key] != int(torrent[key]) for key in ("total_size", "size", "amount_left")):
        return "unknown", 0
    # A fully downloaded selected subset must not inflate the complete seed pool.
    complete = (torrent["progress"] >= 1 and torrent["amount_left"] == 0
                and torrent["size"] == torrent["total_size"] and torrent["total_size"] > 0)
    if not complete or torrent.get("state") not in ACTIVE_STATES:
        return "seedpool_inactive", 0
    return "seedpool_active", int(torrent["total_size"])


def snapshot(torrents, floor_bytes=FLOOR_BYTES, minimum_seed_seconds=MINIMUM_SEED_SECONDS):
    if not isinstance(torrents, list) or not number(floor_bytes) or not number(minimum_seed_seconds):
        raise ValueError("Invalid retention snapshot input")
    result = {"current_active_bytes": 0, "headroom_bytes": 0,
              "below_minimum_count": 0, "below_minimum_bytes": 0,
              "classified_count": 0, "active_count": 0, "unknown_count": 0,
              "inactive_count": 0, "floor_bytes": int(floor_bytes),
              "minimum_seed_seconds": int(minimum_seed_seconds)}
    seen = set()
    for torrent in torrents:
        if not isinstance(torrent, dict):
            result["unknown_count"] += 1
            continue
        identifier = torrent.get("hash")
        if not isinstance(identifier, str) or not HASH.fullmatch(identifier) or identifier.lower() in seen:
            result["unknown_count"] += 1
            continue
        seen.add(identifier.lower())
        kind, size = classify(torrent)
        if kind == "unknown":
            result["unknown_count"] += 1
        elif kind.startswith("seedpool_"):
            result["classified_count"] += 1
            if kind == "seedpool_active":
                result["active_count"] += 1
                result["current_active_bytes"] += size
                if torrent["seeding_time"] < minimum_seed_seconds:
                    result["below_minimum_count"] += 1
                    result["below_minimum_bytes"] += size
            else:
                result["inactive_count"] += 1
    result["headroom_bytes"] = max(0, result["current_active_bytes"] - int(floor_bytes))
    result["retirement_guard_healthy"] = int(result["unknown_count"] == 0)
    return result


def retirement_plan(torrents, requested_hashes, floor_bytes=FLOOR_BYTES,
                    minimum_seed_seconds=MINIMUM_SEED_SECONDS):
    """Evaluate an entire retirement batch, never stop or remove any torrent.

    Newly downloaded replacements count only once present in the live snapshot
    as complete, fully selected and actively seeding. No speculative credits.
    """
    state = snapshot(torrents, floor_bytes, minimum_seed_seconds)
    reasons = Counter()
    if state["unknown_count"]:
        reasons["snapshot_unknown"] += state["unknown_count"]
    if not isinstance(requested_hashes, list) or not requested_hashes:
        reasons["empty_or_invalid_request"] += 1
        requested_hashes = []
    indexed = {t["hash"].lower(): t for t in torrents
               if isinstance(t, dict) and isinstance(t.get("hash"), str)}
    seen = set()
    removal_bytes = 0
    for value in requested_hashes:
        if not isinstance(value, str) or not HASH.fullmatch(value):
            reasons["invalid_identifier"] += 1
            continue
        identifier = value.lower()
        if identifier in seen:
            reasons["duplicate_request"] += 1
            continue
        seen.add(identifier)
        torrent = indexed.get(identifier)
        if torrent is None:
            reasons["torrent_missing"] += 1
            continue
        kind, size = classify(torrent)
        if kind != "seedpool_active":
            reasons["not_active_complete_seedpool"] += 1
            continue
        removal_bytes += size
        if torrent["seeding_time"] < minimum_seed_seconds:
            reasons["minimum_seed_age_not_met"] += 1
    projected = state["current_active_bytes"] - removal_bytes
    if state["current_active_bytes"] < floor_bytes:
        reasons["already_below_floor"] += 1
    if projected < floor_bytes:
        reasons["projected_below_floor"] += 1
    return {"allowed": not bool(reasons), "requested_count": len(requested_hashes),
            "current_active_bytes": state["current_active_bytes"],
            "retiring_bytes": removal_bytes, "projected_active_bytes": projected,
            "floor_bytes": int(floor_bytes),
            "minimum_seed_seconds": int(minimum_seed_seconds),
            "blocked_reasons": dict(sorted(reasons.items()))}
