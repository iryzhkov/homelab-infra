#!/usr/bin/env python3
"""Prove the Garage bootstrap's request signing, lifecycle comparison and convergence plan."""
import copy
import datetime
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from typing import Any
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("garage_bootstrap", str(ROOT / "scripts/garage-bootstrap.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
bootstrap = importlib.util.module_from_spec(spec)
loader.exec_module(bootstrap)

SPEC = json.loads((ROOT / "stacks/objectstore/bootstrap.json").read_text())
MUTATING = {"UpdateClusterLayout", "ApplyClusterLayout", "CreateBucket", "UpdateBucket",
            "CreateKey", "AllowBucketKey", "DenyBucketKey"}


class FakeGarage:
    """An in-memory admin API holding just what the bootstrap reads and writes."""

    def __init__(self):
        self.calls = []
        self.version, self.roles = 0, []
        self.buckets, self.keys = {}, {}

    def __call__(self, endpoint, payload=None) -> Any:
        payload = payload or {}
        self.calls.append(endpoint)
        if endpoint == "GetClusterLayout":
            return {"version": self.version, "roles": copy.deepcopy(self.roles), "stagedRoleChanges": []}
        if endpoint == "GetClusterStatus":
            return {"nodes": [{"id": "node1"}]}
        if endpoint == "UpdateClusterLayout":
            self.roles = payload["roles"]
            return {}
        if endpoint == "ApplyClusterLayout":
            assert payload["version"] == self.version + 1
            self.version += 1
            return {}
        if endpoint == "ListBuckets":
            return [{"id": i, "globalAliases": [b["alias"]]} for i, b in self.buckets.items()]
        if endpoint == "CreateBucket":
            bucket_id = "b%d" % len(self.buckets)
            self.buckets[bucket_id] = {"alias": payload["globalAlias"], "quotas": {"maxSize": None, "maxObjects": None}}
            return {"id": bucket_id, "quotas": self.buckets[bucket_id]["quotas"]}
        if endpoint == "GetBucketInfo":
            return {"id": payload["id"], "quotas": dict(self.buckets[payload["id"]]["quotas"])}
        if endpoint == "UpdateBucket":
            self.buckets[payload["id"]]["quotas"] = dict(payload["body"]["quotas"])
            return {}
        if endpoint == "ListKeys":
            return [{"id": i, "name": k["name"]} for i, k in self.keys.items()]
        if endpoint == "CreateKey":
            key_id = "GK%d" % len(self.keys)
            self.keys[key_id] = {"name": payload["name"], "perms": {}}
            return {"accessKeyId": key_id, "secretAccessKey": "created-secret"}
        if endpoint == "GetKeyInfo":
            key = self.keys[payload["id"]]
            info: dict = {"buckets": [{"id": b, "permissions": dict(p)} for b, p in key["perms"].items()]}
            if payload.get("showSecretKey"):
                info["secretAccessKey"] = "lifecycle-secret"
            return info
        if endpoint in ("AllowBucketKey", "DenyBucketKey"):
            perms = self.keys[payload["accessKeyId"]]["perms"].setdefault(
                payload["bucketId"], {p: False for p in bootstrap.PERMISSIONS})
            for name, flag in payload["permissions"].items():
                if flag:
                    perms[name] = endpoint == "AllowBucketKey"
            return {}
        raise AssertionError("unexpected endpoint " + endpoint)


class FakeS3:
    def __init__(self):
        self.lifecycles, self.secrets = {}, []

    def factory(self, access, secret):
        self.secrets.append(secret)
        return self

    def get_lifecycle(self, bucket):
        return bootstrap.parse_lifecycle(self.lifecycles[bucket]) if bucket in self.lifecycles else []

    def put_lifecycle(self, bucket, body):
        self.lifecycles[bucket] = body


class SigningTest(unittest.TestCase):
    def test_aws_documented_get_object_example(self):
        # "Example: GET Object" from the AWS Signature Version 4 documentation
        # for Amazon S3 (authenticating requests, header-based).
        headers = bootstrap.sign_v4(
            "GET", "https://examplebucket.s3.amazonaws.com/test.txt", {"Range": "bytes=0-9"}, b"",
            "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
            datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc))
        self.assertEqual(
            headers["Authorization"],
            "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
            "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
            "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


class LifecycleTest(unittest.TestCase):
    def test_generated_rule_parses_back(self):
        bucket = {"name": "b", "expire_days": 14, "abort_multipart_days": 1}
        self.assertEqual(bootstrap.parse_lifecycle(bootstrap.lifecycle_xml(bucket)),
                         [("chaldea-retention", "Enabled", "", "14", "1")])

    def test_server_formatting_is_ignored(self):
        body = (b'<?xml version="1.0" encoding="UTF-8"?>\n<LifecycleConfiguration '
                b'xmlns="http://s3.amazonaws.com/doc/2006-03-01/">\n  <Rule>\n    <ID>chaldea-retention</ID>'
                b"<Status>Enabled</Status><Filter><Prefix/></Filter><Expiration><Days>14</Days></Expiration>"
                b"<AbortIncompleteMultipartUpload><DaysAfterInitiation>1</DaysAfterInitiation>"
                b"</AbortIncompleteMultipartUpload></Rule></LifecycleConfiguration>")
        self.assertEqual(bootstrap.parse_lifecycle(body), [("chaldea-retention", "Enabled", "", "14", "1")])


class ConvergeTest(unittest.TestCase):
    def test_check_on_a_fresh_node_reads_nothing_it_cannot(self):
        garage, s3 = FakeGarage(), FakeS3()
        result = bootstrap.converge(garage, SPEC, False, s3.factory)
        self.assertFalse(result["changed"])
        self.assertFalse(MUTATING & set(garage.calls))
        # Before the layout exists Garage refuses listings; a check skips them.
        self.assertNotIn("ListBuckets", garage.calls)
        self.assertEqual(len([a for a in result["actions"] if a.endswith(": create")]),
                         len(SPEC["buckets"]) + len(SPEC["keys"]))

    def test_apply_converges_and_a_second_run_changes_nothing(self):
        garage, s3 = FakeGarage(), FakeS3()
        first = bootstrap.converge(garage, SPEC, True, s3.factory)
        self.assertTrue(first["changed"])
        self.assertEqual(garage.version, 1)
        for bucket in SPEC["buckets"]:
            bucket_id = next(i for i, b in garage.buckets.items() if b["alias"] == bucket["name"])
            self.assertEqual(garage.buckets[bucket_id]["quotas"],
                             {"maxSize": bucket["max_size"], "maxObjects": bucket["max_objects"]})
            self.assertIn(bucket["name"], s3.lifecycles)
        self.assertNotIn("created-secret", json.dumps(first))
        self.assertEqual(set(s3.secrets), {"lifecycle-secret"})
        garage.calls.clear()
        second = bootstrap.converge(garage, SPEC, True, s3.factory)
        self.assertEqual(second["actions"], [])
        self.assertFalse(second["changed"])
        self.assertFalse(MUTATING & set(garage.calls))

    def test_extra_permission_is_revoked_and_strays_are_reported(self):
        garage, s3 = FakeGarage(), FakeS3()
        bootstrap.converge(garage, SPEC, True, s3.factory)
        key_id = next(i for i, k in garage.keys.items() if k["name"] == "chaldea-agent-a")
        for perms in garage.keys[key_id]["perms"].values():
            perms["owner"] = True
        garage.keys["GKstray"] = {"name": "stray", "perms": {}}
        result = bootstrap.converge(garage, SPEC, True, s3.factory)
        self.assertIn("key chaldea-agent-a on chaldea-results: deny owner", result["actions"])
        self.assertEqual(result["unmanaged"], ["key stray"])
        self.assertFalse(any(p["owner"] for p in garage.keys[key_id]["perms"].values()))

    def test_staged_layout_changes_are_refused(self):
        class Staged(FakeGarage):
            def __call__(self, endpoint, payload=None) -> Any:
                answer = super().__call__(endpoint, payload)
                if endpoint == "GetClusterLayout":
                    answer["stagedRoleChanges"] = [{"id": "node1"}]
                return answer

        garage = Staged()
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.converge(garage, SPEC, True, FakeS3().factory)
        self.assertFalse(MUTATING & set(garage.calls))


if __name__ == "__main__":
    unittest.main()
