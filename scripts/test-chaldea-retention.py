#!/usr/bin/env python3
"""Prove every Chaldea stream, bucket and log store has an explicit bound.

The old platform never cleaned up after itself, so a bound is a requirement
here, not a default (docs/chaldea-infra-foundation.md). This checks the
definitions in this repository: the JetStream streams and consumers, the NATS
account limits that enforce a bound on any stream created by hand, the Garage
buckets, Loki's retention, and the Docker log limits of the two stacks. If
nats-server is on PATH the rendered configuration is also parsed by it.

Needs PyYAML and Jinja2 (CI installs ansible-core first).
"""
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
JETSTREAM = ROOT / "stacks/nats/jetstream"
NATS_VARS = yaml.safe_load((ROOT / "inventory/group_vars/swarm/nats.yml").read_text())
HOSTS = NATS_VARS["nats_optional_hosts"]
SIZE = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}


def streams():
    return {p.stem: json.loads(p.read_text()) for p in sorted((JETSTREAM / "streams").glob("*.json"))}


def consumers():
    return {(p.parent.name, p.stem): json.loads(p.read_text())
            for p in sorted((JETSTREAM / "consumers").glob("*/*.json"))}


def fake_user_nkey(seed):
    """A well-formed public user NKey (prefix, 32 bytes, CRC-16) for a test."""
    raw = bytes([20 << 3]) + hashlib.sha256(seed.encode()).digest()
    crc = 0
    for byte in raw:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return base64.b32encode(raw + crc.to_bytes(2, "little")).decode().rstrip("=")


def render_nats_conf():
    keys = {name: fake_user_nkey(name) for name in NATS_VARS["nats_nkeys"]}
    source = (ROOT / "stacks/nats/templates/nats.conf.j2").read_text()
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    return env.from_string(source).render(nats_nkeys=keys, nats_chaldea_hosts=HOSTS)


def account_limit(conf, name):
    """A value from the CHALDEA account's jetstream block, sizes in bytes."""
    block = re.search(r"CHALDEA \{.*?jetstream \{(.*?)\}", conf, re.S)
    assert block, "no jetstream block in the CHALDEA account"
    value = re.search(r"^\s*%s:\s*(\S+)" % name, block.group(1), re.M)
    assert value, "no %s in the CHALDEA account's jetstream block" % name
    match = re.fullmatch(r"(\d+)(KB|MB|GB)?", value.group(1))
    assert match, "%s is not a number or size" % name
    return int(match.group(1)) * SIZE.get(match.group(2) or "", 1)


class StreamTest(unittest.TestCase):
    def test_every_stream_is_bounded_in_size_and_age(self):
        self.assertEqual(set(streams()), {"CHALDEA_WORK", "AKASHA_EVENTS", "KV_CHALDEA_QUOTA"})
        for name, config in streams().items():
            with self.subTest(stream=name):
                self.assertEqual(config["name"], name)
                self.assertGreater(config["max_bytes"], 0)
                self.assertGreater(config["max_age"], 0)
                self.assertGreater(config["max_msg_size"], 0)
                self.assertEqual(config["storage"], "file")
                self.assertEqual(config["num_replicas"], 1)
                self.assertLessEqual(config["duplicate_window"], config["max_age"])

    def test_a_full_work_queue_refuses_work_rather_than_dropping_it(self):
        self.assertEqual(streams()["CHALDEA_WORK"]["retention"], "workqueue")
        self.assertEqual(streams()["CHALDEA_WORK"]["discard"], "new")

    def test_account_limits_cover_every_stream(self):
        conf = render_nats_conf()
        self.assertRegex(conf, r"\n\s*max_bytes_required: true\n")
        total = sum(c["max_bytes"] for c in streams().values())
        self.assertLessEqual(total, account_limit(conf, "max_file"))
        for config in streams().values():
            self.assertLessEqual(config["max_bytes"], account_limit(conf, "store_max_stream_bytes"))
        self.assertLessEqual(len(streams()), account_limit(conf, "max_streams"))


class ConsumerTest(unittest.TestCase):
    def test_one_work_consumer_per_host_and_one_for_the_fleet(self):
        names = {name for stream, name in consumers() if stream == "CHALDEA_WORK"}
        self.assertEqual(names, {"fleet"} | {"host-" + h for h in HOSTS})
        for (stream, name), config in consumers().items():
            with self.subTest(consumer=name):
                self.assertIn(stream, streams())
                self.assertEqual(config["durable_name"], name)
                self.assertEqual(config["name"], name)
                self.assertEqual(config["ack_policy"], "explicit")
                self.assertGreater(config["max_deliver"], 0)
                self.assertGreater(config["max_ack_pending"], 0)
        for host in HOSTS:
            self.assertEqual(consumers()[("CHALDEA_WORK", "host-" + host)]["filter_subject"],
                             "chaldea.work.host.%s.>" % host)

    def test_every_host_user_is_scoped_to_its_own_names(self):
        conf = render_nats_conf()
        for host in HOSTS:
            with self.subTest(host=host):
                self.assertNotIn('"akasha.%s.>"' % host, conf)
                self.assertIn('"$KV.CHALDEA_QUOTA.%s.>"' % host, conf)
                self.assertIn('"$JS.API.CONSUMER.MSG.NEXT.CHALDEA_WORK.host-%s"' % host, conf)
                self.assertIn('subscribe: {allow: ["_INBOX_%s.>"]}' % host, conf)
        self.assertNotIn('"_INBOX.>"', conf)
        self.assertNotIn("password", conf)

    @unittest.skipUnless(shutil.which("nats-server"), "nats-server is not on PATH")
    def test_the_server_accepts_the_rendered_configuration(self):
        with tempfile.NamedTemporaryFile("w", suffix=".conf") as handle:
            handle.write(render_nats_conf())
            handle.flush()
            proc = subprocess.run(["nats-server", "-t", "-c", handle.name], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_the_fake_keys_have_the_shape_the_deploy_requires(self):
        self.assertRegex(fake_user_nkey("x"), r"^U[A-Z2-7]{55}$")


class ObjectStoreTest(unittest.TestCase):
    def test_every_bucket_has_a_quota_and_an_expiry(self):
        spec = json.loads((ROOT / "stacks/objectstore/bootstrap.json").read_text())
        buckets = {b["name"]: b for b in spec["buckets"]}
        for name, bucket in buckets.items():
            with self.subTest(bucket=name):
                for field in ("max_size", "max_objects", "expire_days", "abort_multipart_days"):
                    self.assertIsInstance(bucket[field], int)
                    self.assertGreater(bucket[field], 0)
        self.assertLessEqual(sum(b["max_size"] for b in buckets.values()), spec["layout"]["capacity"])
        keys = {k["name"]: k for k in spec["keys"]}
        for key in keys.values():
            self.assertLessEqual(set(key["buckets"]), set(buckets))
        lifecycle = keys[spec["lifecycle_key"]]["buckets"]
        self.assertTrue(all("owner" in lifecycle[b] for b in buckets))


class LogTest(unittest.TestCase):
    def test_loki_deletes_by_age(self):
        config = yaml.safe_load((ROOT / "stacks/logging/files/loki-config.yml").read_text())
        self.assertTrue(config["compactor"]["retention_enabled"])
        self.assertRegex(config["limits_config"]["retention_period"], r"^\d+[dh]$")

    def test_container_logs_are_bounded(self):
        # A service without its own logging gets swarm_stack_logging
        # (roles/swarm/defaults/main.yml), which always sets max-size.
        for stack in ("nats", "objectstore"):
            document = yaml.safe_load((ROOT / "stacks" / stack / "stack.yml").read_text())
            for name, service in document["services"].items():
                with self.subTest(service=name):
                    logging = service.get("logging")
                    if logging is not None:
                        self.assertIn("max-size", logging.get("options", {}))


if __name__ == "__main__":
    unittest.main()
