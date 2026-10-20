"""roles/authentik_machine_identities: refusals as plain data, and the real role
run by ansible-playbook against a fake Authentik, writing only in a scratch
HOME. Nothing here reaches Authentik, a host or a secret store."""
import getpass
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import fake_authentik as fake  # noqa: E402

_spec = importlib.util.spec_from_file_location("machine_identities", HERE.parent / "filter_plugins/machine_identities.py")
mi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mi)

RESOURCE = "https://jocasta.example.test/mcp"
ACCESS = {"state": "present", "slug": "chaldea-jocasta", "name": "Chaldea Jocasta machine access",
          "group": "chaldea-jocasta-machine-identities", "client_id": "chaldea-jocasta-machine",
          "resource": RESOURCE, "signing_key": fake.KEYPAIR, "scope": "jocasta.read", "token_seconds": 300}
NOW = datetime.now(timezone.utc)
EXPIRES = (NOW + timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")
USER = getpass.getuser()
HOSTS = {"agent-one": {"ssh_user": USER, "home": "/srv/scratch/agent", "connection": "ssh"},
         "laptop-one": {"ssh_user": USER, "home": "", "connection": "local"}}


def scratch_home():
    """Refuse to run anywhere but a scratch HOME under a disk-backed TMPDIR."""
    tmp = Path(os.environ["TMPDIR"]).resolve()
    home = Path(os.environ["HOME"]).resolve()
    assert tmp != Path("/tmp") and not tmp.is_relative_to("/tmp"), "TMPDIR must not be host /tmp"
    assert home.is_relative_to(tmp) and home != tmp, "HOME must be scratch inside TMPDIR"
    return home


def identity(host="agent-one", profile="jocasta-projectA-reporter", project="projectA",
             generation=1, state="present", expires=EXPIRES):
    return {"host": host, "profile": profile, "project": project, "generation": generation,
            "expires": expires, "state": state}


def spec(identities, access=ACCESS, hosts=HOSTS, days=180):
    return mi.machine_identity_spec(identities, access, hosts, days, NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))


class DeclarationTest(unittest.TestCase):
    """What an operator may declare, refused before anything is looked up."""

    def refused(self, fragment, *args, **kwargs):
        with self.assertRaises(mi.AnsibleFilterError) as caught:
            spec(*args, **kwargs)
        self.assertIn(fragment, str(caught.exception))

    def test_names_paths_and_ownership_record(self):
        s = spec([identity(), identity(host="laptop-one", generation=3)])
        agent, laptop = s["identities"]
        self.assertEqual(agent["username"], "chaldea-agent-one-jocasta-projectA-reporter")
        self.assertEqual(agent["secret_path"],
                         "/srv/scratch/agent/.config/chaldea/authentik/jocasta-projectA-reporter.app-password")
        self.assertEqual(laptop["secret_path"],
                         f"/home/{USER}/.config/chaldea/authentik/jocasta-projectA-reporter.app-password")
        self.assertEqual(laptop["old_tokens"], [laptop["username"] + "-g1", laptop["username"] + "-g2"])
        # The Jocasta grant is the service profile's; naming and paths are not.
        self.assertEqual((agent["service"], agent["grant"], agent["grant_attributes"]),
                         ("jocasta", {"project": "projectA"}, {"chaldea_jocasta": {"project": "projectA"}}))
        self.assertNotIn("jocasta", agent["username"] + agent["secret_path"].replace("jocasta-projectA-reporter", ""))
        self.assertIn(f'"aud": "{RESOURCE}"', s["access"]["expression"])
        self.assertNotIn("request", s["access"]["expression"])
        self.assertEqual(mi.machine_identity_owned("version: 1\n" + s["marker"]), s["objects"])

    def test_fixed_scope_lifetime_and_https_resource(self):
        self.refused("scope must be jocasta.read", [], access=ACCESS | {"scope": "jocasta.write"})
        self.refused("token_seconds must be 300", [], access=ACCESS | {"token_seconds": 3600})
        self.refused("https URL", [], access=ACCESS | {"resource": "http://jocasta.example.test/mcp"})
        self.refused("kp_uuid", [], access=ACCESS | {"signing_key": "generate"})
        self.refused("needs exactly", [], access=ACCESS | {"client_secret": "x"})

    def test_destination_must_be_a_known_inventory_host_and_user(self):
        self.refused("not in the inventory", [identity(host="elsewhere")])
        self.refused("ssh_user", [identity()], hosts={"agent-one": {"ssh_user": "root;rm"}})
        self.refused("ssh or be the local controller", [identity()],
                     hosts={"agent-one": {"ssh_user": USER, "connection": "community.proxmox.proxmox_pct_remote"}})
        self.refused("normalized absolute", [identity()],
                     hosts={"agent-one": {"ssh_user": USER, "home": "/home/x/../root"}})

    def test_expiry_window_and_unique_accounts(self):
        soon = (NOW + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.refused("expires within a day", [identity(expires=soon)])
        self.refused("more than 180 days", [identity(expires=(NOW + timedelta(days=400)).strftime("%Y-%m-%dT00:00:00Z"))])
        self.refused("time zone", [identity(expires="2027-01-01T00:00:00")])
        self.refused("declared twice", [identity(), identity()])
        hosts = HOSTS | {"agent": {"ssh_user": USER}}
        self.refused("collides", [identity(host="agent-one", profile="x"), identity(host="agent", profile="one-x")],
                     hosts=hosts)

    def test_shared_application_outlives_its_accounts(self):
        self.refused("must be absent before the shared application is removed",
                     [identity()], access=ACCESS | {"state": "absent"})
        s = spec([identity(state="absent")], access=ACCESS | {"state": "absent"})
        self.assertTrue(all(state == "absent" for _, _, state in s["objects"]))

    def test_foreign_blueprint_content_is_not_trusted(self):
        with self.assertRaises(mi.AnsibleFilterError):
            mi.machine_identity_owned("version: 1\nentries: []")


def lookup(kind, ident, rows, count=None, status=200):
    return {"item": {"kind": kind, "id": ident}, "status": status,
            "json": {"pagination": {"count": len(rows) if count is None else count}, "results": rows}}


class OwnershipTest(unittest.TestCase):
    """Refusals computed from what Authentik reports under each name."""

    def setUp(self):
        self.s = spec([identity()])
        self.user = self.s["identities"][0]["username"]
        self.keypair = {"item": {"kind": "keypair", "id": fake.KEYPAIR}, "status": 200,
                        "json": {"private_key_available": True}}

    def refusals(self, results, owned=()):
        return mi.machine_identity_refusals(self.s, list(owned), list(results) + [self.keypair])

    def test_first_run_on_empty_authentik_is_allowed(self):
        self.assertEqual(self.refusals([]), [])

    def test_existing_object_without_ownership_is_never_adopted(self):
        found = self.refusals([lookup("user", self.user, [{"username": self.user}])])
        self.assertEqual(len(found), 1)
        self.assertIn("refusing to take over", found[0])

    def test_incomplete_or_unexpected_lookups_refuse(self):
        self.assertIn("did not return every candidate",
                      self.refusals([lookup("user", self.user, [], count=3)])[0])
        self.assertIn("unexpected shape", self.refusals([lookup("user", self.user, [], status=403)])[0])

    def test_client_id_and_signing_key_collisions(self):
        clash = lookup("client_id", ACCESS["client_id"], [{"client_id": ACCESS["client_id"], "name": "Grafana"}])
        self.assertIn("belongs to another provider", self.refusals([clash])[0])
        missing = {"item": {"kind": "keypair", "id": fake.KEYPAIR}, "status": 404, "json": {}}
        self.assertIn("no private key", mi.machine_identity_refusals(self.s, [], [missing])[0])

    def test_owned_objects_must_keep_their_binding(self):
        owned = [["token", self.user + "-g1", "present"], ["user", self.user, "present"]]
        token = lookup("token", self.user + "-g1", [{"identifier": self.user + "-g1", "intent": "app_password",
                                                     "user_obj": {"username": "igor"}}])
        self.assertIn("owner or intent", self.refusals([token], owned)[0])
        user = lookup("user", self.user, [{"username": self.user, "type": "service_account",
                                           "is_superuser": True, "roles": [], "groups": []}])
        self.assertIn("superuser state or roles", self.refusals([user], owned)[0])

    def test_dropping_a_created_object_needs_an_absent_declaration_first(self):
        owned = [["user", "chaldea-old-host-reporter", "present"]]
        self.assertIn("declare it absent", self.refusals([], owned)[0])
        gone = [["user", "chaldea-old-host-reporter", "absent"]]
        self.assertEqual(self.refusals([lookup("user", "chaldea-old-host-reporter", [])], gone), [])


class Scratch:
    """One fake Authentik and one scratch inventory with two hosts."""

    def __init__(self, case):
        root = scratch_home() / "machine-identity-tests"
        root.mkdir(exist_ok=True)
        self.base = Path(tempfile.mkdtemp(dir=root))
        self.state = fake.Authentik()
        self.server = fake.serve(self.state)
        case.addCleanup(self.server.server_close)
        case.addCleanup(self.server.shutdown)
        self.homes = {host: self.base / host for host in ("agent-one", "laptop-one")}
        for home in self.homes.values():
            home.mkdir(mode=0o755)
        host = {"ansible_connection": "local", "ssh_user": USER, "ansible_python_interpreter": sys.executable}
        inventory = {"all": {"hosts": {name: host | {"authentik_machine_identity_home": str(home)}
                                        for name, home in self.homes.items()}}}
        self.inventory = self.base / "inventory.yml"
        self.inventory.write_text(yaml.safe_dump(inventory))

    def secret(self, host, profile="jocasta-projectA-reporter"):
        return self.homes[host] / ".config/chaldea/authentik" / (profile + ".app-password")

    def run(self, identities, access=ACCESS, enabled=True, check=False):
        play = [{"name": "Machine identities against a fake Authentik", "hosts": "localhost",
                 "connection": "local", "gather_facts": False,
                 "vars": {"ansible_python_interpreter": sys.executable,
                          "authentik_url": f"http://127.0.0.1:{self.server.server_port}/api/v3",
                          "authentik_headers": {"Authorization": "Bearer " + fake.ADMIN},
                          "authentik_machine_identities_enabled": enabled,
                          "authentik_jocasta_machine_access": access,
                          "authentik_machine_identities": identities},
                 "tasks": [{"name": "Enrol Chaldea machine identities",
                             "ansible.builtin.include_role": {"name": "authentik_machine_identities"},
                             "when": "authentik_machine_identities_enabled | default(false) | bool"}]}]
        path = self.base / "play.yml"
        path.write_text(yaml.safe_dump(play))
        self.state.requests.clear()
        env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"), ANSIBLE_NOCOLOR="1",
                   ANSIBLE_LOCAL_TEMP=str(self.base / "ansible-local"),
                   ANSIBLE_REMOTE_TEMP=str(self.base / "ansible-remote"))
        argv = ["ansible-playbook", "-i", str(self.inventory), str(path), "--diff"] + (["--check"] if check else [])
        result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        output = result.stdout + result.stderr
        assert fake.ADMIN not in output and fake.KEY_PREFIX not in output, "a credential reached the output"
        return result.returncode, output

    def changed(self, output):
        return sum(int(n) for n in re.findall(r"^\S+\s*:\s*ok=\d+\s+changed=(\d+)", output, re.M))

    def writes(self):
        return [(m, p) for m, p, _ in self.state.requests if m != "GET"]

    def key_reads(self):
        return [p for m, p, _ in self.state.requests if p.endswith("/view_key/")]


class RoleTest(unittest.TestCase):
    """The real role, played against the fake."""

    def setUp(self):
        self.x = Scratch(self)

    def ok(self, *args, **kwargs):
        code, output = self.x.run(*args, **kwargs)
        self.assertEqual(code, 0, output)
        return output

    def refused(self, fragment, *args, **kwargs):
        code, output = self.x.run(*args, **kwargs)
        self.assertNotEqual(code, 0, output)
        self.assertIn(fragment, output)
        return output

    def test_playbook_leaves_the_role_off_unless_enabled(self):
        play = yaml.safe_load((ROOT / "playbooks/authentik.yml").read_text())[0]
        task = next(t for t in play["tasks"] if t["name"] == "Enrol Chaldea machine identities")
        self.assertEqual(task["when"], "authentik_machine_identities_enabled | default(false) | bool")
        self.assertIs(yaml.safe_load((ROOT / "playbooks/vars/authentik.yml").read_text())
                      ["authentik_machine_identities_enabled"], False)
        defaults = yaml.safe_load((HERE.parent / "defaults/main.yml").read_text())
        self.assertIs(defaults["authentik_machine_identities_enabled"], False)
        self.assertEqual(defaults["authentik_machine_identities"], [])
        self.ok([identity()], enabled=False)
        self.assertEqual(self.x.state.requests, [])

    def test_check_mode_reads_only_and_shows_the_public_blueprint(self):
        output = self.ok([identity()], check=True)
        self.assertEqual(self.x.writes(), [])
        self.assertEqual(self.x.key_reads(), [])
        self.assertFalse((self.x.homes["agent-one"] / ".config").exists())
        self.assertIn("client_credentials", output)
        self.assertIn("seconds=300", output)

    def test_apply_delivers_once_and_a_second_run_changes_nothing(self):
        self.ok([identity(), identity(host="laptop-one")])
        state = self.x.state
        for host in ("agent-one", "laptop-one"):
            path = self.x.secret(host)
            username = f"chaldea-{host}-jocasta-projectA-reporter"
            self.assertEqual(path.read_text(), state.tokens[username + "-g1"]["key"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            user = state.users[username]
            self.assertEqual((user["type"], user["roles"], user["attributes"]["chaldea_jocasta"]),
                             ("service_account", [], {"project": "projectA"}))
            self.assertEqual(state.tokens[username + "-g1"]["intent"], "app_password")
        provider = state.providers[ACCESS["name"]]
        self.assertEqual((provider["grant_types"], provider["access_token_validity"], provider["signing_key"],
                          provider["client_id"]),
                         (["client_credentials"], "seconds=300", fake.KEYPAIR, ACCESS["client_id"]))
        self.assertEqual(sorted(state.groups[ACCESS["group"]]["users"]),
                         ["chaldea-agent-one-jocasta-projectA-reporter", "chaldea-laptop-one-jocasta-projectA-reporter"])
        stored = next(iter(state.blueprints.values()))["content"]
        self.assertNotIn(fake.KEY_PREFIX, stored)
        self.assertNotIn("client_secret", stored)
        before = {h: (self.x.secret(h).read_text(), self.x.secret(h).stat().st_ino) for h in self.x.homes}

        output = self.ok([identity(), identity(host="laptop-one")])
        self.assertEqual(self.x.changed(output), 0, output)
        self.assertEqual([m for m, _ in self.x.writes()], ["POST"])  # the apply call only
        self.assertEqual({h: (self.x.secret(h).read_text(), self.x.secret(h).stat().st_ino) for h in self.x.homes},
                         before)

    def test_existing_account_with_the_same_name_is_not_taken_over(self):
        self.x.state.add_user("chaldea-agent-one-jocasta-projectA-reporter")
        self.refused("refusing to take over", [identity()])
        self.assertEqual(self.x.writes(), [])
        self.assertEqual(self.x.key_reads(), [])

    def test_unsafe_destination_stops_before_authentik_changes(self):
        target = self.x.base / "elsewhere"
        target.write_text("untouched")
        for path in self.x.secret("agent-one").parents:
            if path == self.x.homes["agent-one"]:
                break
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o700)
        self.x.secret("agent-one").symlink_to(target)
        self.refused("single-link regular file", [identity()])
        self.assertEqual(self.x.writes(), [])
        self.assertEqual(target.read_text(), "untouched")

    def test_rotation_issues_a_new_key_and_deletes_the_old_token(self):
        self.ok([identity()])
        first = self.x.secret("agent-one").read_text()
        self.ok([identity(generation=2)])
        tokens = self.x.state.tokens
        name = "chaldea-agent-one-jocasta-projectA-reporter"
        self.assertNotIn(name + "-g1", tokens)
        self.assertEqual(self.x.secret("agent-one").read_text(), tokens[name + "-g2"]["key"])
        self.assertNotEqual(self.x.secret("agent-one").read_text(), first)

    def test_absent_identity_removes_only_its_own_account_token_and_file(self):
        self.ok([identity(), identity(host="laptop-one")])
        laptop = self.x.secret("laptop-one").read_text()
        self.ok([identity(state="absent"), identity(host="laptop-one")])
        state = self.x.state
        self.assertNotIn("chaldea-agent-one-jocasta-projectA-reporter", state.users)
        self.assertFalse(any(t.startswith("chaldea-agent-one") for t in state.tokens))
        self.assertFalse(self.x.secret("agent-one").exists())
        self.assertEqual(self.x.secret("laptop-one").read_text(), laptop)
        self.assertIn(ACCESS["name"], state.providers)
        self.assertEqual(state.groups[ACCESS["group"]]["users"], ["chaldea-laptop-one-jocasta-projectA-reporter"])
        self.assertIn("igor", state.users)

    def test_removing_everything_leaves_other_applications(self):
        self.ok([identity()])
        gone = ACCESS | {"state": "absent"}
        self.ok([identity(state="absent")], access=gone)
        state = self.x.state
        self.assertEqual((state.providers, state.mappings), ({}, {}))
        self.assertEqual(sorted(state.apps), ["budget"])
        self.assertNotIn(ACCESS["group"], state.groups)
        self.assertEqual(state.groups["family"]["users"], ["igor"])
        output = self.ok([identity(state="absent")], access=gone)
        self.assertEqual(self.x.changed(output), 0, output)

    def test_owner_drift_is_refused_before_any_key_is_read(self):
        self.ok([identity()])
        self.x.state.tokens["chaldea-agent-one-jocasta-projectA-reporter-g1"]["user"] = "igor"
        self.refused("owner or intent", [identity()])
        self.assertEqual(self.x.key_reads(), [])
        self.assertEqual(self.x.writes(), [])

    def test_dropping_a_present_identity_is_refused(self):
        self.ok([identity(), identity(host="laptop-one")])
        self.refused("declare it absent", [identity(host="laptop-one")])
        self.assertEqual(self.x.writes(), [])

    def test_an_application_open_to_everyone_blocks_delivery(self):
        self.x.state.apps["wiki"] = {"pk": "wiki-pk", "slug": "wiki", "name": "Wiki", "provider": None,
                                     "policy_engine_mode": "any", "bindings": []}
        self.refused("access answer for wiki is True", [identity()])
        self.assertEqual(self.x.key_reads(), [])
        self.assertFalse(self.x.secret("agent-one").exists())


if __name__ == "__main__":
    unittest.main()
