#!/usr/bin/env python3
"""Re-embed a private legacy collection without changing record metadata.

Pinned to OpenViking 0.5.2. This tool never opens the retained source database:
it snapshots it into a private work directory before using the offline SDK.
"""
import argparse
import collections
import fcntl
import hashlib
import importlib.metadata
import json
import math
import logging
import os
from pathlib import Path
import shutil
import sys
import urllib.parse
import urllib.request

SDK_VERSION = "0.5.2"
MODEL = "qwen3-embedding:0.6b"
MODEL_DIGEST = "d84267e5df12e19a8be357ab23b88966d61137226724f399707dee270cebccd3"
LONG_URIS = {
    "/agent/ffb1327b18bf/memories/cases",
    "/user/default/memories/entities",
    "/user/default/memories/events",
    "/user/default/memories/preferences",
}


class MigrationError(Exception):
    """Safe, content-free diagnostic."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".new")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, sort_keys=True, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def tree_hash(root):
    hashes = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise MigrationError("symlink in input tree")
        if path.is_file():
            hashes[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return digest(encoded(hashes))


def disjoint(paths):
    resolved = [p.resolve() for p in paths]
    for i, path in enumerate(resolved):
        for other in resolved[i + 1:]:
            if path == other or path in other.parents or other in path.parents:
                raise MigrationError("source, files, work and publication paths must be disjoint")


def source_path(record, files):
    uri = record["uri"]
    if uri.startswith("viking://"):
        uri = uri[len("viking://"):]
    else:
        uri = uri.lstrip("/")
    account = record["account_id"]
    if not account or "/" in account or account in {".", ".."}:
        raise MigrationError("invalid account path")
    parts = uri.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise MigrationError("invalid legacy URI")
    level = record["level"]
    if type(level) is not int or level not in (0, 1, 2):
        raise MigrationError("unsupported legacy level")
    relative = Path(account, *parts)
    if level < 2:
        relative /= (".abstract.md", ".overview.md")[level]
    path = files / relative
    if path.resolve() != files.resolve() / relative:
        raise MigrationError("source path follows symlink")
    return path, relative.as_posix()


def verify_manifest(files, manifest):
    for relative, expected in manifest.items():
        path = files / relative
        if (path.resolve() != files.resolve() / relative or not path.is_file()
                or digest(path.read_bytes()) != expected):
            raise MigrationError("original manifest preservation failed")


def nonvector(record):
    return {k: v for k, v in record.items() if k != "vector"}


def validate_vector(vector):
    if not isinstance(vector, list) or len(vector) != 1024:
        raise MigrationError("embedding dimension mismatch")
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
        raise MigrationError("invalid embedding values")
    norm = math.sqrt(sum(x * x for x in vector))
    if abs(norm - 1) > 0.001:
        raise MigrationError("embedding is not normalized")


def request_json(endpoint, route, data=None):
    body = None if data is None else encoded(data)
    request = urllib.request.Request(endpoint + route, body, {"Content-Type": "application/json"})
    # A fixed loopback endpoint must never follow redirects or consult proxy settings.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=180) as response:
            return json.load(response)
    except Exception:
        raise MigrationError("embedding request failed; response body suppressed") from None


def embed(endpoint, text, context):
    response = request_json(endpoint, "/api/embed", {
        "model": MODEL, "input": text, "truncate": False,
        "options": {"num_ctx": context}, "keep_alive": "10m",
    })
    vectors = response.get("embeddings", [])
    if len(vectors) != 1:
        raise MigrationError("embedding response cardinality mismatch")
    vector = vectors[0]
    validate_vector(vector)
    tokens = response.get("prompt_eval_count")
    if type(tokens) is not int or not 0 < tokens <= context:
        raise MigrationError("invalid embedding token accounting")
    return vector, tokens


def physical_rows(collection_path):
    """Decode physical candidates without filling absent legacy scalar fields."""
    from openviking.storage.vectordb.engine import PersistStore
    from openviking.storage.vectordb.store.data import CandidateData
    store = PersistStore(str(collection_path / "store"))
    try:
        rows = [CandidateData.from_bytes(value) for _, value in store.seek_range("C", "D")]
    finally:
        del store
    ids = [json.loads(row.fields)["id"] for row in rows]
    if len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i for i in ids):
        raise MigrationError("invalid or duplicate physical record IDs")
    return dict(zip(ids, rows))


def physical_ids(collection_path):
    return sorted(physical_rows(collection_path))


def raw_metadata(rows):
    # Preserve field presence and serialized scalar JSON as well as its values.
    return {i: {"fields": row.fields, "label": row.label,
                "sparse_raw_terms": list(row.sparse_raw_terms),
                "sparse_values": list(row.sparse_values), "expire_ns_ts": row.expire_ns_ts}
            for i, row in rows.items()}


def fetch(collection, ids):
    rows = collection.fetch_data(ids)
    if rows.ids_not_exist or len(rows.items) != len(ids):
        raise MigrationError("SDK fetch did not preserve physical row coverage")
    records = {item.id: item.fields for item in rows.items}
    if set(records) != set(ids) or any(records[i]["id"] != i for i in ids):
        raise MigrationError("SDK record ID mismatch")
    return records


def inventory(records):
    return {
        "records": len(records),
        "context_types": dict(collections.Counter(r["context_type"] for r in records.values())),
        "levels": dict(collections.Counter(str(r["level"]) for r in records.values())),
        "identity_groups": len({encoded({k: r.get(k) for k in (
            "account_id", "owner_space", "owner_user_id", "acl_mode",
            "acl_direct_grants", "acl_inherited_grants")}) for r in records.values()}),
        "metadata_sha256": digest(encoded({i: nonvector(r) for i, r in records.items()})),
    }


def migrate(args):
    if importlib.metadata.version("openviking") != SDK_VERSION:
        raise MigrationError("requires exact OpenViking 0.5.2")
    parsed = urllib.parse.urlsplit(args.endpoint)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise MigrationError("embedding endpoint must be fixed HTTP IPv4 loopback")
    disjoint([args.source, args.files, args.work, args.publish])
    if args.publish.exists():
        raise MigrationError("publication destination already exists")
    args.work.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(args.work, 0o700)
    with (args.work / "migration.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return migrate_locked(args)


def migrate_locked(args):
    # The SDK logs entire rejected records at ERROR, including private text.
    logging.disable(logging.CRITICAL)
    from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection
    original_hash = tree_hash(args.source)
    manifest = json.loads(args.manifest.read_text())
    verify_manifest(args.files, manifest)
    exclusions = json.loads(args.exclusions.read_text()) if args.exclusions else {}
    binding = {"source_sha256": original_hash, "manifest_sha256": digest(encoded(manifest)),
               "exclusions": exclusions, "sdk": SDK_VERSION, "model_digest": MODEL_DIGEST,
               "long_overviews_8192": args.long_overviews_8192}
    binding_path = args.work / "binding.json"
    if binding_path.exists():
        if json.loads(binding_path.read_text()) != binding:
            raise MigrationError("resume inputs differ")
    else:
        if any(p.name != "migration.lock" for p in args.work.iterdir()):
            raise MigrationError("work directory is not empty")
        atomic_json(binding_path, binding)
    snapshot = args.work / "old-snapshot"
    if not (args.work / "snapshot-ready.json").exists():
        if snapshot.exists():
            shutil.rmtree(snapshot)
        shutil.copytree(args.source, snapshot)
        if tree_hash(snapshot) != original_hash:
            raise MigrationError("source changed during snapshot")
        atomic_json(args.work / "snapshot-ready.json", {"sha256": original_hash})
    ids = physical_ids(snapshot / "context")
    old = get_or_create_local_collection(path=str(snapshot / "context"))
    try:
        records = fetch(old, ids)
    finally:
        old.close()
    texts, excluded, originals = {}, {}, []
    for identity, record in records.items():
        path, relative = source_path(record, args.files)
        if identity in exclusions:
            reason = exclusions[identity]
            if (path.exists() or relative in manifest or not isinstance(reason, str)
                    or not reason.strip()):
                raise MigrationError("exclusion is not a proven absent nonoriginal fixture")
            excluded[identity] = {"reason": reason, "relative_path": relative}
            continue
        if not path.is_file():
            raise MigrationError("source file missing for record " + identity)
        raw = path.read_bytes()
        if relative in manifest:
            if digest(raw) != manifest[relative]:
                raise MigrationError("original source hash changed")
            originals.append(identity)
        texts[identity] = (raw.decode("utf-8"), digest(raw))
    if set(exclusions) != set(excluded):
        raise MigrationError("exclusion refers to unknown record")
    tags = request_json(args.endpoint, "/api/tags")
    if not any(m.get("name") == MODEL and m.get("digest") == MODEL_DIGEST
               for m in tags.get("models", [])):
        raise MigrationError("embedding model provenance mismatch")
    cache = args.work / "embeddings"
    cache.mkdir(mode=0o700, exist_ok=True)
    tokens = collections.Counter()
    for identity, (text, text_hash) in texts.items():
        # Cache filenames never use record IDs as paths.
        path = cache / (digest(identity.encode()) + ".json")
        record = records[identity]
        uri = "/" + record["uri"].removeprefix("viking://").lstrip("/")
        context = 8192 if args.long_overviews_8192 and record["level"] == 1 and uri in LONG_URIS else 4096
        key = {"text_sha256": text_hash, "metadata_sha256": digest(encoded(nonvector(record))),
               "context": context, "model_digest": MODEL_DIGEST}
        prior = json.loads(path.read_text()) if path.exists() else None
        if prior and prior["key"] != key:
            raise MigrationError("resume document or metadata changed")
        if prior and prior["status"] == "complete":
            validate_vector(prior["vector"])
        else:
            if prior and not args.retry_uncertain:
                raise MigrationError("uncertain stateless embedding; explicit --retry-uncertain required")
            atomic_json(path, {"key": key, "status": "requesting"})
            vector, count = embed(args.endpoint, text, context)
            prior = {"key": key, "status": "complete", "vector": vector, "tokens": count}
            atomic_json(path, prior)
        tokens[str(context)] += 1
    # A completed embedding cache can be resumed without repeating remote requests.
    candidate = args.work / "candidate"
    if candidate.exists():
        shutil.rmtree(candidate)
    shutil.copytree(snapshot, candidate)
    # Collection.update_data fills absent legacy scalar fields. The pinned
    # store API retains the original CandidateData.fields JSON byte-for-byte,
    # and emits SDK delta records that collection recovery replays into indexes.
    from openviking.storage.vectordb.store.store_manager import create_store_manager
    raw_before = physical_rows(snapshot / "context")
    expected_raw = {i: row for i, row in raw_before.items() if i in texts}
    manager = create_store_manager("local", str(candidate / "context" / "store"))
    try:
        if excluded:
            manager.delete_data([raw_before[i].label for i in excluded])
        for identity in texts:
            cached = json.loads((cache / (digest(identity.encode()) + ".json")).read_text())
            row = raw_before[identity]
            row.vector = cached["vector"]
            manager.add_cands_data([row])
    finally:
        del manager
    collection = get_or_create_local_collection(path=str(candidate / "context"))
    try:
        after = fetch(collection, sorted(texts))
        expected = {i: records[i] for i in texts}
        if inventory(after) != inventory(expected):
            raise MigrationError("record metadata changed during SDK update")
        for identity, record in after.items():
            validate_vector(record["vector"])
            cached = json.loads((cache / (digest(identity.encode()) + ".json")).read_text())
            if max(abs(a - b) for a, b in zip(record["vector"], cached["vector"])) > 1e-7:
                raise MigrationError("SDK vector round trip differs")
        # Collection provenance must identify the new embedding space.
        metadata = collection.get_meta_data()
        description = metadata.get("Description", "").split("\n\n[openviking.embedding]")[0]
        provenance = {"dimension": 1024, "model": MODEL, "model_identity": MODEL,
                      "provider": "openai"}
        collection.update(description=description + "\n\n[openviking.embedding]\n" +
                          json.dumps(provenance, sort_keys=True))
    finally:
        collection.close()
    raw_after = physical_rows(candidate / "context")
    if raw_metadata(raw_after) != raw_metadata(expected_raw):
        raise MigrationError("raw scalar, sparse or identity metadata changed")
    actual_ids = sorted(raw_after)
    if set(actual_ids) != set(texts):
        raise MigrationError("candidate physical row coverage differs")
    for identity, (_, expected_hash) in texts.items():
        path, _ = source_path(records[identity], args.files)
        if digest(path.read_bytes()) != expected_hash:
            raise MigrationError("source file changed during embedding")
    verify_manifest(args.files, manifest)
    if tree_hash(args.source) != original_hash:
        raise MigrationError("retained source database changed")
    report = {"sdk": SDK_VERSION, "model": MODEL, "model_digest": MODEL_DIGEST,
              "source_sha256": original_hash, "before": inventory(records),
              "after": inventory(after), "original_records_preserved": len(originals),
              "excluded": excluded, "contexts": dict(tokens), "metadata_equal": True,
              "originals_unchanged": True, "manifest_files_preserved": len(manifest),
              "raw_metadata_equal": True,
              "raw_metadata_sha256": digest(encoded(raw_metadata(raw_after))),
              "publication": str(args.publish)}
    atomic_json(args.work / "report.json", report)
    # Rename is deliberately same-filesystem and refuses any existing destination.
    # Serialize publication ownership with the private parent lock.
    with (args.publish.parent / ".ov-vector-publication.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.publish.exists():
            raise MigrationError("publication destination appeared")
        os.rename(candidate, args.publish)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "files", "manifest", "work", "publish"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--exclusions", type=Path)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--long-overviews-8192", action="store_true")
    parser.add_argument("--retry-uncertain", action="store_true")
    args = parser.parse_args()
    try:
        report = migrate(args)
    except Exception as error:
        # SDK/HTTP exceptions can contain private input, so never echo them.
        print(json.dumps({"status": "blocked", "reason": str(error) if isinstance(
            error, MigrationError) else "offline SDK operation failed; details suppressed"}),
              file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
