#!/usr/bin/env python3
"""Synthetic producer/consumer checks. No credentials or live network access."""
import contextlib
import copy
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("experience", Path(__file__).with_name("emby-experience.py"))
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SERVER = "a" * 32
UID = "b" * 32
OTHER = "c" * 32


class Fake:
    def __init__(self, rows):
        self.rows = {UID: copy.deepcopy(rows), OTHER: copy.deepcopy(rows)}
        self.users = [{"Id": UID, "Name": "synthetic", "Configuration": {"ResumeRewindSeconds": 5}},
                      {"Id": OTHER, "Name": "other", "Configuration": {}}]
        self.calls = []
        self.concurrent = None

    def call(self, route, method="GET", body=None):
        self.calls.append((method, route, copy.deepcopy(body)))
        if route == "Users":
            return copy.deepcopy(self.users)
        if route == "System/Info/Public":
            return {"Id": SERVER}
        if route == "Library/VirtualFolders":
            return [{"Name": "Movies", "ItemId": "movies", "LibraryOptions": {"ImportCollections": True}}]
        parts = route.split("/")
        if parts[0] != "Users" or parts[1] not in self.rows:
            raise AssertionError("Forbidden backend route: " + route)
        uid = parts[1]
        if len(parts) == 2 and method == "GET":
            return copy.deepcopy(next(u for u in self.users if u["Id"] == uid))
        tail = "/".join(parts[2:])
        if tail.startswith("Views?") and method == "GET":
            return {"Items": [{"Id": "movies", "Name": "Movies"}]}
        if tail == "HomeSections?isEditMode=true" and method == "GET":
            if self.concurrent:
                edit, self.concurrent = self.concurrent, None
                edit(self.rows[uid])
            return copy.deepcopy(self.rows[uid])
        if tail == "HomeSections" and method == "POST":
            assert body is not None
            index = next(i for i, r in enumerate(self.rows[uid]) if r["Id"] == body["Id"])
            self.rows[uid][index].update(copy.deepcopy(body))
            return None
        if tail == "HomeSections/Move" and method == "POST":
            assert body is not None
            row = next(r for r in self.rows[uid] if r["Id"] == body["ids"][0])
            self.rows[uid].remove(row)
            self.rows[uid].insert(body["newIndex"], row)
            return None
        raise AssertionError("Forbidden backend route: " + method + " " + route)


def inputs():
    recipes = [{"key": k, "SectionType": "items", "CustomName": k} for k in "abc"]
    desired = {"profiles": ["synthetic"], "sections": recipes, "playback": {}, "importMovieCollections": True}
    ownership = {"version": 1, "server_id": SERVER, "profiles": {
        "synthetic": {"user_id": UID, "rows": {k: k for k in "abc"}}}}
    rows = [{"Id": k, "SectionType": "items", "CustomName": k,
             "ItemTypes": [], "IncludeNextUpInResume": True, "ScrollDirection": "Horizontal",
             "SortOrder": "Ascending", "Query": {}, "ExcludedFolders": [], "ViewType": None} for k in "abc"]
    return desired, ownership, rows


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        # Refuse any scratch creation unless Chaldea supplied the intended HOME.
        expected = str(Path(__file__).resolve().parents[1] / ".cache/holonet-executor/p0-tests/home")
        self.assertEqual(os.environ.get("HOME"), expected)
        self.assertEqual(os.environ.get("TMPDIR"), expected + "/tmp")
        self.assertTrue(Path(expected).is_dir())
        self.assertTrue(Path(expected + "/tmp").is_dir())
        self.assertEqual(tempfile.gettempdir(), expected + "/tmp")
        self.desired, self.ownership, self.rows = inputs()

    def run_helper(self, api, apply=True):
        with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as backup, contextlib.redirect_stdout(io.StringIO()) as output:
            module.main(api, self.desired, self.ownership, apply, backup)
        return json.loads(output.getvalue())

    def writes(self, api):
        return [(m, r, b) for m, r, b in api.calls if m != "GET"]

    def assert_refused(self, api):
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_helper(api)
        self.assertEqual(self.writes(api), [])

    def test_all_baseline_permutations_keep_unknown_slots_and_repeat(self):
        manual = {"Id": "manual", "SectionType": "items", "CustomName": "a", "Query": {"private": ["x"]}}
        generated = {"Id": "generated", "SectionType": "items", "CustomName": "weekly", "foreign": True}
        for perm in itertools.permutations(self.rows):
            with self.subTest(order=[r["Id"] for r in perm]):
                api = Fake([perm[0], manual, perm[1], generated, perm[2]])
                before_other = copy.deepcopy(api.rows[OTHER])
                self.run_helper(api)
                self.assertEqual([r["Id"] for r in api.rows[UID]], ["a", "manual", "b", "generated", "c"])
                self.assertEqual(api.rows[UID][1], manual)
                self.assertEqual(api.rows[UID][3], generated)
                self.assertEqual(api.rows[OTHER], before_other)
                for method, route, body in self.writes(api):
                    self.assertIn(route, ["Users/" + UID + "/HomeSections", "Users/" + UID + "/HomeSections/Move"])
                    self.assertIn(body.get("Id", body.get("ids", [None])[0]), "abc")
                api.calls.clear()
                self.assertEqual(self.run_helper(api)["changed"], 0)
                self.assertEqual(self.writes(api), [])

    def test_preview_no_writes(self):
        api = Fake(list(reversed(self.rows)))
        before = copy.deepcopy(api.rows)
        self.assertGreater(self.run_helper(api, False)["changed"], 0)
        self.assertEqual(api.rows, before)
        self.assertEqual(self.writes(api), [])

    def test_owned_content_update_retains_id_and_unowned_duplicate_title(self):
        self.rows[0]["CustomName"] = "manual rename"
        manual = {"Id": "manual", "SectionType": "items", "CustomName": "a"}
        api = Fake(self.rows + [manual])
        self.run_helper(api)
        self.assertEqual(api.rows[UID][0]["Id"], "a")
        self.assertEqual(api.rows[UID][0]["CustomName"], "a")
        self.assertEqual(api.rows[UID][-1], manual)

    def test_missing_or_wrong_ownership_refuses_before_mutation(self):
        for field, value in [("version", True), ("version", 2), ("server_id", "d" * 32), ("profiles", {})]:
            self.desired, self.ownership, self.rows = inputs()
            self.ownership[field] = value
            self.assert_refused(Fake(self.rows))
        self.ownership = {}
        self.assert_refused(Fake(self.rows))

    def test_wrong_user_or_duplicate_owned_or_missing_recipe_key(self):
        for patch in [{"user_id": OTHER}, {"rows": {"a": "a", "b": "a", "c": "c"}}, {"rows": {"a": "a"}}]:
            self.desired, self.ownership, self.rows = inputs()
            self.ownership["profiles"]["synthetic"].update(patch)
            self.assert_refused(Fake(self.rows))

    def test_duplicate_or_missing_server_id(self):
        self.assert_refused(Fake(self.rows + [copy.deepcopy(self.rows[0])]))
        self.assert_refused(Fake(self.rows[1:]))

    def test_later_profile_invalid_preflights_before_any_mutation(self):
        self.desired["profiles"].append("other")
        self.rows[0]["CustomName"] = "requires update"
        api = Fake(self.rows)
        self.ownership["profiles"]["other"] = {"user_id": OTHER, "rows": {"a": "a", "b": "b", "c": "missing"}}
        self.assert_refused(api)

    def test_concurrent_manual_edit_stops_before_first_write(self):
        self.rows[0]["CustomName"] = "requires update"
        api = Fake(self.rows)
        original = api.call
        reads = 0
        def call(route, method="GET", body=None):
            nonlocal reads
            if route == "Users/" + UID + "/HomeSections?isEditMode=true":
                reads += 1
                if reads == 2:
                    api.rows[UID].append({"Id": "new-manual", "SectionType": "items"})
            return original(route, method, body)
        api.call = call
        self.assert_refused(api)

    def test_playbook_consumes_tested_script_and_ownership_input(self):
        import yaml
        playbook = yaml.safe_load(Path(__file__).parents[1].joinpath("playbooks/emby-experience.yml").read_text())
        task = playbook[0]["tasks"][0]
        self.assertIn("/../scripts/emby-experience.py", task["ansible.builtin.command"]["stdin"])
        self.assertIn("emby_experience_owned_rows", task["environment"]["EMBY_EXPERIENCE_OWNERSHIP"])
        self.assertTrue(task["no_log"])


if __name__ == "__main__":
    unittest.main()
