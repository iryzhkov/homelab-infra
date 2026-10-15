#!/usr/bin/env python3
"""Fast, read-only anime pack discovery through one canonical query per tracker."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import sys
import time
from typing import Any
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

LIMIT = 100


class API:
    def __init__(self, root, stack, port, version):
        config = ET.parse(root / stack / "config/config.xml").getroot()
        self.key = (config.findtext("ApiKey") or "").strip()
        if not self.key:
            raise RuntimeError("Missing local API key")
        self.base = ("http://127.0.0.1:" + str(port)
                     + (config.findtext("UrlBase") or "").rstrip("/") + "/api/" + version + "/")

    def get(self, endpoint) -> Any:
        request = urllib.request.Request(self.base + endpoint, headers={"X-Api-Key": self.key})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError("API request returned HTTP " + str(error.code)) from None
        except Exception:
            raise RuntimeError("API read failed or timed out") from None


def clean(value, secrets=()):
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(?:https?://|magnet:)[^\s]+", "[link omitted]", text)
    text = re.sub(r"(?i)\b[a-f0-9]{40,64}\b", "[hash omitted]", text)
    text = re.sub(r"(?i)(?:api[_-]?key|token|password)\s*[=:]\s*[^\s&]+", "[credential omitted]", text)
    return text[:512]


def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def classify(title, series, season):
    """Classify discovery hints; never infer verified identity or download acceptance."""
    flags = []
    aliases = [series["title"], series.get("originalTitle") or ""]
    aliases.extend(a.get("title", "") for a in series.get("alternateTitles", []))
    normalized_title = normalized(title)
    if not any(normalized(a) and normalized(a) in normalized_title for a in aliases):
        flags.append("title_identity_unconfirmed")
    years = sorted(set(int(y) for y in re.findall(
        r"(?<![A-Za-z0-9])(?:19\d{2}|20[0-3]\d)(?![A-Za-z0-9])", title)))
    if years and series.get("year") not in years:
        flags.append("release_year_differs_from_series_premiere")
    if not years:
        flags.append("release_year_unspecified")
    numbered_episode = bool(re.search(r"(?i)\bS\d{1,2}[ ._-]*E\d+", title))
    season_numbers = [int(n) for n in re.findall(r"(?i)\b(?:S|Season[ ._-]*)(\d{1,2})(?!\d)", title)]
    absolute_batch = bool(re.search(r"(?i)(?:\[|\(|\b)\d{1,3}\s*[-~]\s*\d{1,3}(?:\]|\)|\b)", title))
    complete = bool(re.search(r"(?i)\b(?:complete|batch)\b", title))
    film = bool(re.search(r"(?i)\b(?:movie|film)\b", title))
    if numbered_episode:
        kind = "episode"
    elif film:
        kind = "movie_or_special"
    elif season_numbers:
        kind = "season_pack"
    elif absolute_batch or complete:
        kind = "absolute_or_complete_batch"
    else:
        kind = "unclassified"
    if season_numbers:
        season_status = "explicit_requested_season" if set(season_numbers) == {season} else "other_or_multiple_seasons"
        if season_status != "explicit_requested_season":
            flags.append("season_identity_requires_review")
    else:
        season_status = "season_unspecified"
        flags.append("season_identity_unconfirmed")
    flags.append("audio_quality_size_and_identity_require_manual_review")
    return {"release_kind": kind, "season_status": season_status,
            "release_years": years, "identity_review_flags": flags}


def tracker_search(api, indexer, title):
    query = urllib.parse.urlencode({"query": title, "indexerIds": indexer["id"],
        "type": "search", "categories": [5000, 5070], "limit": LIMIT}, doseq=True)
    start = time.monotonic()
    rows = api.get("search?" + query)
    if not isinstance(rows, list):
        raise RuntimeError("Invalid tracker response")
    return rows, round(time.monotonic() - start, 3)


def discover(sonarr, prowlarr, series_id, season):
    started = time.monotonic()
    series = sonarr.get("series/" + str(series_id))
    if series.get("seriesType") != "anime":
        raise RuntimeError("Selected Sonarr series must have anime series type")
    if not any(s.get("seasonNumber") == season for s in series.get("seasons", [])):
        raise RuntimeError("Requested season is absent from the Sonarr catalog")
    catalog_title = series["title"]
    if not isinstance(catalog_title, str) or not catalog_title.strip():
        raise RuntimeError("Missing catalog title")
    indexers = prowlarr.get("indexer")
    selected = []
    for token in ("seedpool", "nyaa"):
        matches = [i for i in indexers if token in i["name"].lower() and i.get("enable", True)]
        if len(matches) != 1:
            raise RuntimeError("Expected exactly one enabled " + token + " indexer")
        selected.append(matches[0])
    secrets = (sonarr.key, prowlarr.key)
    result = {"series_id": series_id, "catalog_title": clean(catalog_title, secrets),
        "catalog_year": series.get("year"), "season": season, "canonical_queries_per_indexer": 1,
        "manual_review_only": True, "tracker_results": [], "packs": [], "errors": []}
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = {executor.submit(tracker_search, prowlarr, i, catalog_title): i for i in selected}
        for future in as_completed(pending):
            indexer = pending[future]
            indexer_name = clean(indexer["name"], secrets)
            try:
                rows, elapsed = future.result()
                excluded = 0
                for raw in rows:
                    title = clean(raw.get("title"), secrets)
                    classification = classify(title, series, season)
                    if classification["release_kind"] not in ("season_pack", "absolute_or_complete_batch"):
                        excluded += 1
                        continue
                    # Only these scalar metadata fields leave this function.
                    result["packs"].append({"title": title,
                        "size_bytes": int(raw.get("size") or 0), "indexer": indexer_name,
                        "seeders": int(raw.get("seeders") or 0), "catalog_year": series.get("year"),
                        **classification})
                result["tracker_results"].append({"indexer": indexer_name, "seconds": elapsed,
                    "raw_count": len(rows), "non_pack_count": excluded,
                    "possibly_truncated": len(rows) >= LIMIT, "requested_limit": LIMIT})
            except Exception as error:
                message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                result["errors"].append({"indexer": indexer_name, "error": clean(message, secrets)})
    result["packs"].sort(key=lambda p: (
        p["season_status"] != "explicit_requested_season",
        "title_identity_unconfirmed" in p["identity_review_flags"], -p["seeders"]))
    result["seconds"] = round(time.monotonic() - started, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series-id", type=int, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--state-root", type=Path, default=Path("/srv/swarm"))
    args = parser.parse_args()
    if args.series_id <= 0 or args.season <= 0:
        parser.error("series-id and season must be positive")
    result = discover(API(args.state_root, "sonarr-tv", 8989, "v3"),
                      API(args.state_root, "prowlarr", 9696, "v1"), args.series_id, args.season)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["errors"]:
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": str(error) if isinstance(error, RuntimeError)
                          else type(error).__name__}), file=sys.stderr)
        sys.exit(1)
