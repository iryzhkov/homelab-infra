#!/usr/bin/env python3
"""Migration safety and pinned offline-SDK round-trip tests."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("migration", Path(__file__).with_name("ov-vector-migration.py"))
assert spec is not None and spec.loader is not None
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        home = Path.home()
        assert str(home).endswith("/.cache/ov-selfhost-20261024/test-home"), "scratch HOME required"
        scratch = Path(os.environ["TMPDIR"])
        assert str(scratch).startswith(str(home)) and "/tmp" not in str(scratch)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = self.root / "files"
        self.files.mkdir()

    def test_paths_and_metadata(self):
        record = {"account_id": "default", "uri": "/agent/a/memories/n.md", "level": 2}
        path, rel = m.source_path(record, self.files)
        self.assertEqual(rel, "default/agent/a/memories/n.md")
        self.assertEqual(path, self.files / rel)
        for change in ({"uri": "/agent/../secret"}, {"account_id": ".."}, {"level": -1}):
            with self.assertRaises(m.MigrationError):
                m.source_path(dict(record, **change), self.files)
        with self.assertRaises(m.MigrationError):
            m.disjoint([self.root, self.files])
        link = self.files / "default"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(m.MigrationError):
            m.source_path(record, self.files)

    def test_invalid_vectors(self):
        for vector in ([0.0] * 1024, [float("nan")] * 1024, [1.0], [True] * 1024):
            with self.assertRaises(m.MigrationError):
                m.validate_vector(vector)

    def test_real_sdk_lossless_and_resume(self):
        from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection
        source = self.root / "old"
        source.mkdir()
        types_by_name = [
            ("id", "string"), ("uri", "path"), ("type", "string"), ("context_type", "string"),
            ("vector", "vector"), ("sparse_vector", "sparse_vector"), ("created_at", "date_time"),
            ("updated_at", "date_time"), ("active_count", "int64"), ("parent_uri", "path"),
            ("level", "int64"), ("name", "string"), ("description", "string"), ("tags", "string"),
            ("abstract", "string"), ("account_id", "string"), ("owner_space", "string"),
            ("search_tags", "list<string>"), ("content", "text"), ("md5", "string"),
            ("owner_user_id", "string"), ("acl_mode", "string"),
            ("acl_direct_grants", "list<string>"), ("acl_inherited_grants", "list<string>"),
        ]
        fields = [{"FieldName": name, "FieldType": typ, **({"IsPrimaryKey": True} if name == "id"
                  else {"Dim": 1024} if name == "vector" else {})} for name, typ in types_by_name]
        collection = get_or_create_local_collection(
            meta_data={"CollectionName": "context", "Fields": fields}, path=str(source / "context"))
        self.addCleanup(collection.close)
        records = []
        for identity, uri, level, context_type in [
            ("original", "/agent/a/memories/n.md", 2, "memory"),
            ("long", "/user/default/memories/events", 1, "memory"),
            ("stale", "/resources/fixture", 0, "resource"),
            ("skill", "/agent/a/skills/example", 0, "skill"),
        ]:
            record = {name: ([] if typ == "list<string>" else {} if typ == "sparse_vector"
                      else 1 if typ == "int64" else "2026-10-24T00:00:00Z" if typ == "date_time" else "")
                      for name, typ in types_by_name}
            record.update(id=identity, uri=uri, level=level, context_type=context_type,
                          account_id="default", owner_space="a", owner_user_id="owner",
                          acl_mode="private", acl_direct_grants=["user:owner"],
                          vector=[1.0] + [0.0] * 1023, type="file",
                          content="retained legacy field", abstract="retained legacy abstract")
            records.append(record)
        collection.upsert_data(records)
        collection.close()
        # Legacy rows omit fields that high-level update_data silently fills.
        from openviking.storage.vectordb.store.store_manager import create_store_manager
        raw = m.physical_rows(source / "context")
        scalar = json.loads(raw["original"].fields)
        for key in ("owner_user_id", "acl_mode", "acl_direct_grants",
                    "acl_inherited_grants", "content", "md5", "search_tags"):
            scalar.pop(key)
        raw["original"].fields = json.dumps(scalar)
        manager = create_store_manager("local", str(source / "context/store"))
        manager.add_cands_data([raw["original"]])
        del manager
        expected_raw = {i: value for i, value in m.raw_metadata(
            m.physical_rows(source / "context")).items() if i != "stale"}
        before = m.tree_hash(source)
        manifest = {}
        for record in records:
            if record["id"] == "stale":
                continue
            path, rel = m.source_path(record, self.files)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("full untouched text " + record["id"])
            manifest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        exclusions = self.root / "exclusions.json"
        exclusions.write_text(json.dumps({"stale": "isolated fixture never in original manifest"}))
        args = types.SimpleNamespace(source=source, files=self.files, manifest=manifest_path,
            exclusions=exclusions, work=self.root / "work", publish=self.root / "published",
            endpoint="http://127.0.0.1:19000", long_overviews_8192=True, retry_uncertain=False)
        original_path, _ = m.source_path(records[0], self.files)
        saved = original_path.read_bytes()
        original_path.unlink()
        with self.assertRaisesRegex(m.MigrationError, "original manifest preservation failed"):
            m.migrate(args)
        original_path.write_bytes(saved)
        calls = []
        def fake_embed(endpoint, text, context):
            calls.append(context)
            if len(calls) == 2:
                raise m.MigrationError("interrupted")
            return [0.0, 1.0] + [0.0] * 1022, 20
        tags = {"models": [{"name": m.MODEL, "digest": m.MODEL_DIGEST}]}
        with mock.patch.object(m, "request_json", return_value=tags), mock.patch.object(m, "embed", fake_embed):
            with self.assertRaisesRegex(m.MigrationError, "interrupted"):
                m.migrate(args)
            self.assertFalse(args.publish.exists())
            with self.assertRaisesRegex(m.MigrationError, "uncertain"):
                m.migrate(args)
        args.retry_uncertain = True
        with mock.patch.object(m, "request_json", return_value=tags), mock.patch.object(
                m, "embed", return_value=([0.0, 1.0] + [0.0] * 1022, 20)) as embed:
            report = m.migrate(args)
        self.assertEqual(embed.call_count, 2)
        self.assertEqual(report["before"]["records"], 4)
        self.assertEqual(report["after"]["records"], 3)
        self.assertEqual(report["after"]["context_types"], {"memory": 2, "skill": 1})
        self.assertEqual(report["contexts"], {"4096": 2, "8192": 1})
        self.assertEqual(m.tree_hash(source), before)
        self.assertEqual(m.physical_ids(args.publish / "context"), ["long", "original", "skill"])
        self.assertTrue(report["raw_metadata_equal"])
        self.assertEqual(m.raw_metadata(m.physical_rows(args.publish / "context")), expected_raw)
        with self.assertRaisesRegex(m.MigrationError, "already exists"):
            m.migrate(args)


if __name__ == "__main__":
    unittest.main()
