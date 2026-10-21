#!/usr/bin/env python3
"""Exercise canonical disabled enrollment and actual MESSAGES grants in scratch."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

import jinja2
import nats
import nkeys
import yaml

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = yaml.safe_load((ROOT / "inventory/group_vars/swarm/nats.yml").read_text())
TEMPLATE = (ROOT / "stacks/nats/templates/nats.conf.j2").read_text()
DEFINITIONS = ROOT / "stacks/nats/messaging"
COMMAND = "chaldea.message.command.pc.agent_a"
RECEIPT = "chaldea.message.receipt.pc.agent_a"


def identity():
    seed = nkeys.encode_seed(os.urandom(32), nkeys.PREFIX_BYTE_USER)
    return seed, nkeys.from_seed(seed).public_key.decode()


def render(values):
    return jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(TEMPLATE).render(**values)


class EnrollmentTest(unittest.TestCase):
    def test_real_ordered_guards_refuse_before_host_effects(self):
        tasks = yaml.safe_load((ROOT / "stacks/nats/swarm-prepare.yml").read_text())
        start = next(i for i, task in enumerate(tasks)
                     if task.get("name") == "Validate explicit messaging enrollment before host effects")
        stop = next(i for i, task in enumerate(tasks)
                    if task.get("name") == "Preserve maintenance guard before NATS deployment")
        guards = tasks[start:stop]
        keys = {name: identity()[1] for name in ("admin", "pc", "agent-a")}
        cases = [
            ({}, True),
            ({"nats_messages_enabled": False}, True),
            ({"nats_messages_enabled": "false"}, False),
            ({"nats_messages_enabled": True, "nats_messages_nkeys": keys}, True),
            ({"nats_messages_enabled": False, "nats_messages_nkeys": keys}, False),
            ({"nats_messages_enabled": True}, False),
            ({"nats_messages_enabled": True, "nats_messages_nkeys": []}, False),
            ({"nats_messages_enabled": True, "nats_messages_nkeys": {**keys, "extra": identity()[1]}}, False),
            ({"nats_messages_enabled": True, "nats_messages_nkeys": {**keys, "pc": keys["agent-a"]}}, False),
            ({"nats_messages_enabled": True, "nats_messages_nkeys": {**keys, "pc": INVENTORY["nats_nkeys"]["akasha"]}}, False),
            ({"nats_messages_enabled": True, "nats_messages_nkeys": {**keys, "pc": "not-a-public-key"}}, False),
        ]
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for index, (values, accepted) in enumerate(cases):
                with self.subTest(index=index):
                    marker = directory / ("effect-" + str(index))
                    play = directory / "guard.yml"
                    play.write_text(yaml.safe_dump([{
                        "hosts": "localhost", "gather_facts": False,
                        "vars": {**INVENTORY, **values},
                        "tasks": guards + [{"ansible.builtin.copy": {
                            "content": "effect", "dest": str(marker), "mode": "0600"}}],
                    }]))
                    result = subprocess.run(
                        ["ansible-playbook", "-i", "localhost,", "-c", "local", str(play)],
                        env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(directory / "ansible"),
                             "ANSIBLE_REMOTE_TEMP": str(directory / "remote")},
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0 if accepted else 2, result.stdout + result.stderr)
                    self.assertEqual(marker.exists(), accepted)

    def test_default_does_not_enroll_or_expand_existing_apply_selection(self):
        self.assertIs(INVENTORY["nats_messages_enabled"], False)
        self.assertEqual(INVENTORY["nats_messages_nkeys"], {})
        self.assertNotIn("MESSAGES {", render(INVENTORY))
        values = {k: v for k, v in INVENTORY.items() if not k.startswith("nats_messages_")}
        self.assertEqual(render(INVENTORY), render(values))
        # The existing script rejects messaging before any CLI call, even --all
        # walks only the distinct jetstream directory.
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/nats-jetstream-apply.sh"),
             "--check", "--stream", "MESSAGE_COMMANDS"],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "NATS_BIN": "/unavailable-no-rpc"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown stream", result.stderr)
        self.assertEqual(sorted(p.name for p in (ROOT / "stacks/nats/jetstream/streams").glob("*.json")),
                         ["AKASHA_EVENTS.json"])


class MessagingBrokerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.seeds, self.keys = {}, {}
        for name in ("sys-admin", "chaldea-admin", "akasha", "admin", "pc", "agent-a"):
            seed, self.keys[name] = identity()
            path = self.directory / (name + ".nk")
            path.write_bytes(seed)
            path.chmod(0o600)
            self.seeds[name] = str(path)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = "nats://127.0.0.1:" + str(port)
        values = {**INVENTORY, "nats_nkeys": {name: self.keys[name] for name in INVENTORY["nats_nkeys"]},
                  "nats_messages_enabled": True,
                  "nats_messages_nkeys": {name: self.keys[name] for name in ("admin", "pc", "agent-a")}}
        conf = render(values).replace('listen: "0.0.0.0:4222"', 'listen: "127.0.0.1:%d"' % port)
        conf = conf.replace('http: "0.0.0.0:8222"', 'http: "127.0.0.1:0"')
        conf = conf.replace('listen: "0.0.0.0:9222"', 'listen: "127.0.0.1:0"')
        conf = conf.replace("store_dir: /data/jetstream", "store_dir: " + str(self.directory / "js"))
        path = self.directory / "nats.conf"
        path.write_text(conf)
        self.log = (self.directory / "server.log").open("w")
        self.addCleanup(self.log.close)
        self.server = subprocess.Popen(["nats-server", "-c", str(path)], stdout=self.log, stderr=self.log)
        self.addCleanup(self.stop_server)
        for _ in range(100):
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                if self.server.poll() is not None:
                    self.fail((self.directory / "server.log").read_text())
                await asyncio.sleep(0.02)
        else:
            self.fail("scratch broker startup deadline")
        self.clients = []
        self.addAsyncCleanup(self.close_clients)
        self.admin, _ = await self.connect("admin")
        self.js = self.admin.jetstream()
        for path in sorted((DEFINITIONS / "streams").glob("*.json")):
            data = json.loads(path.read_text())
            # JSON nanoseconds are protocol units. Use the wire API directly
            # so the tested definitions are exactly the deployed JSON bytes.
            response = await self.admin.request("$JS.API.STREAM.CREATE." + data["name"], path.read_bytes(), timeout=2)
            self.assertNotIn("error", json.loads(response.data))
        for path in sorted((DEFINITIONS / "consumers").glob("*/*.json")):
            data = json.loads(path.read_text())
            response = await self.admin.request(
                "$JS.API.CONSUMER.DURABLE.CREATE." + path.parent.name + "." + data["durable_name"],
                json.dumps({"stream_name": path.parent.name, "config": data}).encode(), timeout=2)
            self.assertNotIn("error", json.loads(response.data))

    def stop_server(self):
        if self.server.poll() is None:
            self.server.terminate()
            self.server.wait(timeout=5)

    async def close_clients(self):
        for client in self.clients:
            await client.close()

    async def connect(self, name):
        errors = asyncio.Queue()

        async def on_error(error):
            errors.put_nowait(error)

        prefix = {"pc": "_INBOX_message.pc", "agent-a": "_INBOX_message.agent_a"}.get(name, "_INBOX_" + name)
        client = await nats.connect(self.url, nkeys_seed=self.seeds[name], inbox_prefix=prefix.encode(),
                                    error_cb=on_error, allow_reconnect=False, connect_timeout=2)
        self.clients.append(client)
        return client, errors

    async def pull(self, client, stream, consumer):
        # Pull delivery retains the stored original subject. A general request
        # multiplexer routes by response subject and cannot correlate that.
        inbox = client.new_inbox()
        subscription = await client.subscribe(inbox)
        try:
            await client.flush()
            await client.publish("$JS.API.CONSUMER.MSG.NEXT." + stream + "." + consumer,
                                 b'{"batch":1,"expires":1000000000}', reply=inbox)
            return await subscription.next_msg(timeout=2)
        finally:
            await subscription.unsubscribe()

    async def denied(self, client, errors, subject, subscribe=False):
        if subscribe:
            await client.subscribe(subject)
        else:
            await client.publish(subject, b"{}")
        await client.flush()
        error = await asyncio.wait_for(errors.get(), 2)
        self.assertIn("permissions violation", str(error).lower())

    async def test_actual_canonical_route_bounds_and_account_isolation(self):
        pc, pc_errors = await self.connect("pc")
        target, target_errors = await self.connect("agent-a")
        ack = await pc.jetstream().publish(COMMAND, b"fixed-harmless-command")
        self.assertEqual(ack.stream, "MESSAGE_COMMANDS")
        message = await self.pull(target, "MESSAGE_COMMANDS", "receive_pc_agent_a")
        self.assertEqual(message.data, b"fixed-harmless-command")
        await target.publish(message.reply)
        await target.jetstream().publish(RECEIPT, b"accepted")
        receipt = await self.pull(pc, "MESSAGE_RECEIPTS", "source_pc_agent_a")
        self.assertEqual(receipt.data, b"accepted")
        await pc.publish(receipt.reply)
        await pc.flush()
        for client, errors, subjects, foreign_inbox in [
            (pc, pc_errors, [RECEIPT, "chaldea.message.command.pc.other",
                             "$JS.API.CONSUMER.MSG.NEXT.MESSAGE_COMMANDS.receive_pc_agent_a",
                             "$JS.ACK.MESSAGE_COMMANDS.receive_pc_agent_a.1.1.1.1.1"],
             "_INBOX_message.agent_a.foreign"),
            (target, target_errors, [COMMAND, "chaldea.message.receipt.pc.other",
                                     "$JS.API.CONSUMER.MSG.NEXT.MESSAGE_RECEIPTS.source_pc_agent_a",
                                     "$JS.ACK.MESSAGE_RECEIPTS.source_pc_agent_a.1.1.1.1.1"],
             "_INBOX_message.pc.foreign"),
        ]:
            for subject in subjects + ["akasha.forged.event", "$JS.API.INFO",
                                        "$JS.API.STREAM.INFO.MESSAGE_COMMANDS",
                                        "$JS.API.STREAM.CREATE.EVIL",
                                        "$JS.API.CONSUMER.CREATE.MESSAGE_COMMANDS.evil",
                                        "$JS.API.DIRECT.GET.MESSAGE_COMMANDS",
                                        "$JS.API.STREAM.MSG.GET.MESSAGE_COMMANDS",
                                        "_INBOX_other.reply"]:
                await self.denied(client, errors, subject)
            await self.denied(client, errors, foreign_inbox, subscribe=True)
        chaldea, _ = await self.connect("chaldea-admin")
        self.assertEqual(await chaldea.jetstream().streams_info(), [])
        self.assertEqual(sorted(s.config.name for s in await self.js.streams_info()),
                         ["MESSAGE_COMMANDS", "MESSAGE_RECEIPTS"])
        for name, age, consumer in [("MESSAGE_COMMANDS", 86400, "receive_pc_agent_a"),
                                     ("MESSAGE_RECEIPTS", 120, "source_pc_agent_a")]:
            stream = await self.js.stream_info(name)
            self.assertEqual(stream.config.max_bytes, 1048576)
            self.assertEqual(stream.config.max_msgs, 256)
            self.assertEqual(stream.config.max_msg_size, 8192)
            self.assertEqual(stream.config.max_msgs_per_subject, 32)
            self.assertEqual(stream.config.max_age, age)
            self.assertEqual(stream.config.retention, "workqueue")
            self.assertEqual(stream.config.discard, "new")
            self.assertTrue(stream.config.discard_new_per_subject)
            self.assertFalse(stream.config.allow_direct)
            config = (await self.js.consumer_info(name, consumer)).config
            self.assertEqual(config.ack_wait, 60)
            self.assertEqual(config.max_deliver, 5)
            self.assertEqual(config.max_ack_pending, 32)
            self.assertEqual(config.max_waiting, 32)
            self.assertEqual(config.max_batch, 1)
            self.assertEqual(config.max_expires, 1)
        with self.assertRaises(nats.js.errors.APIError):
            await pc.jetstream().publish(COMMAND, b"x" * 8193)
        for _ in range(32):
            await pc.jetstream().publish(COMMAND, b"bounded")
        with self.assertRaises(nats.js.errors.APIError):
            await pc.jetstream().publish(COMMAND, b"full")
        self.assertEqual((await self.js.stream_info("MESSAGE_COMMANDS")).state.messages, 32)


if __name__ == "__main__":
    scratch = Path(os.environ["TMPDIR"]).resolve()
    assert Path.home().resolve().is_relative_to(scratch), "HOME must be inside task TMPDIR"
    assert not str(scratch).startswith("/tmp/"), "Use disk scratch"
    if not shutil.which("nats-server"):
        raise SystemExit("Pinned nats-server 2.15.1 required")
    os.umask(0o077)
    proof = ROOT / "nats-messaging-proof.json"
    proof.write_text(json.dumps({"passed": False, "liveEnrollment": False}) + "\n")
    result = unittest.main(exit=False).result
    proof.write_text(json.dumps({"passed": result.wasSuccessful(), "testsRun": result.testsRun,
                                 "canonicalConfig": True, "liveEnrollment": False,
                                 "targetLocalRenewal": False}) + "\n")
    raise SystemExit(0 if result.wasSuccessful() else 1)
