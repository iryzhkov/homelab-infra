"""Recording adapter tests. Run only in the documented Chaldea scratch HOME."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from emby_operator_recommendations import (DONE_LIMIT, RETIRE_AT, V2_FIELDS, Journal,
                                           NotDispatched, Operator, Refused, digest,
                                           native_move_index, require)

SERVER, ACTOR, IGOR = "a" * 32, "b" * 32, "c" * 32
SCOPE = dict(server=SERVER, actor=ACTOR, recipient=IGOR, library="10", recipe="igor-movies")


class RecordingAPI:
    session_binding = "synthetic-session-instance"

    def __init__(self, directory):
        self.directory = directory
        self.identity = dict(SCOPE, verified=True, is_admin=True,
                             auth_mode="authenticated-admin-session",
                             session_binding=self.session_binding)
        self.identity.pop("recipe")
        self.home = [{"Id": 1, "SectionType": "unknown", "Unknown": {"preserve": [9]}}]
        self.playlists = []
        self.visible = ["101", "102"]
        self.writes, self.queries = [], []
        self.fail_before = self.fail_after = None
        self.serial = 0
        self.media = {"101": b"movie-a", "102": b"movie-b"}
        self.other_home = [{"Id": 55, "untouched": True}]
        # Server-added fields on a created row; move tests use a fixed-field row.
        self.row_extra = {"UnknownDefault": "retained"}
        self.move_effect = None
        # Optional normal-to-edit Home projection; by default both views are equal.
        self.edit_view = None

    def verified_identity(self):
        return copy.deepcopy(self.identity)

    def snapshot(self, recipient, marker):
        assert recipient == IGOR
        return copy.deepcopy(dict(home=self.home, playlists=self.playlists))

    def query_movies(self, recipient, library, ids):
        assert recipient == IGOR and library == "10"
        self.queries.append(list(ids))
        return [dict(Id=m, Type="Movie", LibraryId=library) for m in ids if m in self.visible]

    def mutate(self, op, args, action):
        pending = json.loads((self.directory / "journal.json").read_text())["pending"]
        assert pending["operation"] == op, "Intent must be durable before dispatch"
        self.writes.append((op, copy.deepcopy(args)))
        if self.fail_before == op:
            raise TimeoutError("Uncertain dispatch before server action")
        action()
        if self.fail_after == op:
            raise TimeoutError("Response lost after commit")

    def create_playlist(self, actor, marker):
        assert actor == ACTOR
        self.mutate("create", marker, lambda: self.playlists.append(
            dict(id="900", marker=marker, owner=actor, access=[], entries=[])))

    def share_read(self, playlist, recipient):
        assert playlist == "900" and recipient == IGOR
        self.mutate("share", [playlist, recipient], lambda: self.playlists[0].update(access=[recipient]))

    def create_row(self, recipient, body):
        assert recipient == IGOR and "Id" not in body
        self.mutate("row", body, lambda: self.home.append(dict(body, Id=2, **self.row_extra)))

    def delete_entries(self, playlist, actor, entries):
        assert playlist == "900" and actor == ACTOR
        assert entries == [e["entry"] for e in self.playlists[0]["entries"]]
        self.mutate("clear", entries, lambda: self.playlists[0].update(entries=[]))

    def add_movies(self, playlist, actor, movies):
        assert playlist == "900" and actor == ACTOR
        assert not self.playlists[0]["entries"]
        def add():
            self.serial += 1
            self.playlists[0]["entries"] = [dict(movie=m, entry=f"e{self.serial}_{n}")
                                             for n, m in enumerate(movies)]
        self.mutate("add", movies, add)

    def delete_row(self, recipient, row):
        assert recipient == IGOR and row == 2
        self.mutate("unrow", row, lambda: self.home.remove(next(r for r in self.home if r["Id"] == row)))

    def move_row(self, recipient, args):
        assert recipient == IGOR and set(args) == {"row", "position"}
        def move():
            if self.move_effect:
                return self.move_effect(self.home)
            index = [r["Id"] for r in self.home].index(args["row"])
            self.home.insert(args["position"], self.home.pop(index))
        self.mutate("move", args, move)

    def move_index(self, recipient, home, row, position):
        assert recipient == IGOR
        edit = self.edit_view(copy.deepcopy(home)) if self.edit_view else copy.deepcopy(home)
        return native_move_index(home, edit, row, position)

    def delete_owned_playlist(self, playlist, marker, actor):
        assert playlist == "900" and actor == ACTOR
        assert self.playlists[0]["marker"] == marker
        self.mutate("delete", [playlist, marker, actor], self.playlists.clear)


class OperatorTests(unittest.TestCase):
    def setUp(self):
        expected = Path(__file__).resolve().parents[1] / ".cache/holonet-operator-tests/home"
        self.assertEqual(os.environ.get("HOME"), str(expected))
        self.assertEqual(os.environ.get("TMPDIR"), str(expected / "tmp"))
        self.assertTrue((expected / "tmp").is_dir())
        self.tmp = tempfile.TemporaryDirectory(dir=expected / "tmp")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.directory.chmod(0o700)
        self.api = RecordingAPI(self.directory)
        self.operator = Operator(self.api, Journal(self.directory), **SCOPE)

    def apply(self, key="first", movies=None):
        movies = movies or ["101"]
        p = self.operator.preview(movies)
        return self.operator.apply(key, movies, p["baseline_digest"])

    def test_full_lifecycle_exact_inverse_media_and_other_home_untouched(self):
        before = copy.deepcopy((self.api.home, self.api.other_home, self.api.media))
        self.assertEqual(self.apply()["writes"], 4)
        row = copy.deepcopy(self.api.home[-1])
        self.apply("update", ["102", "101"])
        self.assertEqual(self.api.home[-1], row)
        self.assertEqual([e["movie"] for e in self.api.playlists[0]["entries"]], ["102", "101"])
        p = self.operator.preview(["101"])
        self.operator.remove("remove", p["baseline_digest"])
        self.assertEqual((self.api.home, self.api.other_home, self.api.media), before)
        self.assertEqual(self.api.playlists, [])
        self.assertEqual(self.operator.reconcile(), {"state": "clean"})
        self.assertEqual([op for op, _ in self.api.writes],
                         ["create", "share", "row", "add", "clear", "add", "unrow", "delete"])

    def test_show_empty_is_bounded_and_does_not_create_journal(self):
        result = self.operator.show()
        self.assertEqual(result['playlists'], [])
        self.assertEqual(result['row'], dict(saved_id=None, saved=None, observed=[]))
        self.assertEqual(result['reconcile'], {'state': 'clean'})
        self.assertFalse((self.directory / 'journal.json').exists())
        self.assertEqual(self.api.writes, [])

    def test_show_retained_v1_digests_relation_and_detached_occurrences(self):
        self.apply(movies=['102', '101'])
        before = (self.directory / 'journal.json').read_bytes()
        stored = json.loads(before)
        self.assertNotIn('display_title', stored)
        writes = copy.deepcopy(self.api.writes)
        result = self.operator.show()
        self.assertEqual(result['display_title'], self.operator.marker)
        self.assertEqual(result['marker'], self.operator.marker)
        self.assertEqual(result['scope'], SCOPE)
        self.assertEqual(result['playlists'], stored['current']['playlists'])
        self.assertEqual(result['row']['saved_id'], 2)
        self.assertEqual(result['row']['saved']['ParentId'], '900')
        self.assertEqual(result['row']['observed'], [stored['current']['home'][-1]])
        self.assertEqual(result['revision'], digest(stored['current']))
        self.assertEqual(result['journal_digest'], digest(stored))
        self.assertIsNone(result['pending'])
        self.assertIsNone(result['run'])
        self.assertEqual(result['done_count'], 1)
        result['playlists'][0]['entries'].clear()
        self.assertEqual(len(self.api.playlists[0]['entries']), 2)
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.assertEqual(self.api.writes, writes)

    def test_show_pending_create_before_after_without_adoption_or_journal_write(self):
        self.api.fail_before = 'create'
        with self.assertRaises(TimeoutError):
            self.apply()
        before = (self.directory / 'journal.json').read_bytes()
        self.assertEqual(self.operator.show()['reconcile'], {'state': 'unresolved'})
        self.api.fail_before = None
        # Model the previously uncertain create taking effect, never dispatch it again.
        self.api.playlists = [dict(id='900', marker=self.operator.marker,
                                  owner=ACTOR, access=[], entries=[])]
        result = self.operator.show()
        self.assertEqual(result['reconcile'], {'state': 'observed-applied'})
        self.assertEqual(result['pending'], {'operation': 'create'})
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.assertEqual(len(self.api.writes), 1)
        self.api.home[0]['manual'] = True
        with self.assertRaises(Refused):
            self.operator.show()

    def test_show_refuses_manual_state_and_foreign_identity(self):
        self.apply()
        before = (self.directory / 'journal.json').read_bytes()
        current = copy.deepcopy((self.api.home, self.api.playlists))
        for mutate in (lambda: self.api.playlists[0].update(id='foreign'),
                       lambda: self.api.playlists[0].update(marker='foreign'),
                       lambda: self.api.playlists[0].update(owner=IGOR),
                       lambda: self.api.home[-1].update(CustomName='manual'),
                       lambda: self.api.playlists[0]['entries'][0].update(entry='manual'),
                       lambda: self.api.playlists.append(copy.deepcopy(self.api.playlists[0]))):
            self.api.home, self.api.playlists = copy.deepcopy(current)
            mutate()
            with self.assertRaises(Refused):
                self.operator.show()
        self.assertEqual((self.directory / 'journal.json').read_bytes(), before)
        self.assertEqual(len(self.api.writes), 4)

    def test_identity_refusals_before_writes(self):
        for field, value in [("is_admin", False), ("verified", False), ("actor", "d" * 32),
                             ("server", "d" * 32), ("recipient", "d" * 32),
                             ("library", "11"), ("session_binding", "other-session"),
                             ("auth_mode", "global-api-key")]:
            original = copy.deepcopy(self.api.identity)
            self.api.identity[field] = value
            with self.subTest(field=field), self.assertRaises(Refused):
                self.apply()
            self.api.identity = original
        self.assertEqual(self.api.writes, [])
        self.assertFalse((self.directory / "journal.json").exists())

    def test_blocked_wrong_type_library_and_order_prevent_changes(self):
        self.api.visible = ["101"]
        with self.assertRaises(Refused):
            self.apply(movies=["101", "102"])
        original = self.api.query_movies
        for bad in [[dict(Id="101", Type="Episode", LibraryId="10")],
                    [dict(Id="101", Type="Movie", LibraryId="11")], []]:
            self.api.query_movies = lambda *args: bad
            with self.assertRaises(Refused):
                self.apply()
        self.api.query_movies = original
        self.assertEqual(self.api.writes, [])

    def test_same_key_replay_no_writes_and_key_conflict(self):
        self.apply()
        before = copy.deepcopy(self.api.writes)
        self.assertEqual(self.operator.apply("first", ["101"]), {"state": "replayed", "writes": 0})
        with self.assertRaises(Refused):
            self.operator.apply("first", ["102"])
        self.assertEqual(self.api.writes, before)

    def test_uncertain_create_not_applied_never_retried(self):
        self.api.fail_before = "create"
        with self.assertRaises(TimeoutError):
            self.apply()
        journal = (self.directory / "journal.json").read_bytes()
        self.assertEqual(self.operator.reconcile(), {"state": "unresolved", "operation": "create"})
        self.assertEqual((self.directory / "journal.json").read_bytes(), journal)
        self.api.fail_before = None
        with self.assertRaises(Refused):
            self.operator.apply("first", ["101"])
        self.assertEqual([op for op, _ in self.api.writes], ["create"])

    def test_lost_add_response_exact_readback_only(self):
        self.api.fail_after = "add"
        with self.assertRaises(TimeoutError):
            self.apply()
        before = copy.deepcopy(self.api.writes)
        self.assertEqual(self.operator.reconcile(), {"state": "observed-applied", "operation": "add"})
        self.api.fail_after = None
        result = self.operator.apply("first", ["101"])
        self.assertEqual(result["writes"], 0)
        self.assertEqual(before, self.api.writes)

    def test_manual_home_and_playlist_drift_preserved(self):
        self.apply()
        for edit, undo in [
            (lambda: self.api.home[0].update(Unknown="manual"),
             lambda: self.api.home[0].update(Unknown={"preserve": [9]})),
            (lambda: self.api.playlists[0]["entries"].append(dict(movie="102", entry="manual")),
             lambda: self.api.playlists[0]["entries"].pop())]:
            edit()
            before = copy.deepcopy((self.api.home, self.api.playlists, self.api.writes))
            self.assertEqual(self.operator.reconcile(), {"state": "drift"})
            with self.assertRaises(Refused):
                self.operator.remove("remove", digest(dict(home=self.api.home, playlists=self.api.playlists)))
            self.assertEqual((self.api.home, self.api.playlists, self.api.writes), before)
            undo()

    def test_occurrences_reloaded_after_remap(self):
        self.apply()
        self.apply("second", ["102"])
        self.apply("third", ["101"])
        clears = [args for op, args in self.api.writes if op == "clear"]
        self.assertEqual(clears, [["e1_0"], ["e2_0"]])

    def test_duplicate_inputs_and_occurrence_ids_refused(self):
        for values in [["101", "101"], ["../101"], [True], ["101"] * 26, []]:
            with self.assertRaises(Refused):
                self.operator.preview(values)
        self.apply()
        self.api.playlists[0]["entries"].append(dict(movie="102", entry="e1_0"))
        with self.assertRaises(Refused):
            self.operator.reconcile()
        self.assertEqual(len(self.api.writes), 4)

    def test_duplicate_media_occurrences_are_drift_not_adopted(self):
        self.apply()
        self.api.playlists[0]["entries"].append(dict(movie="101", entry="unique-extra"))
        with self.assertRaises(Refused):
            self.operator.apply("next", ["102"], digest(dict(
                home=self.api.home, playlists=self.api.playlists)))
        self.assertEqual(len(self.api.writes), 4)

    def test_wrong_query_order_refused(self):
        self.api.query_movies = lambda *args: [
            dict(Id=m, Type="Movie", LibraryId="10") for m in ["102", "101"]]
        with self.assertRaises(Refused):
            self.apply(movies=["101", "102"])
        self.assertEqual(self.api.writes, [])

    def test_lost_response_with_manual_home_change_remains_unresolved(self):
        self.api.fail_after = "create"
        with self.assertRaises(TimeoutError):
            self.apply()
        self.api.home[0]["manual"] = True
        self.assertEqual(self.operator.reconcile(), {"state": "unresolved", "operation": "create"})
        with self.assertRaises(Refused):
            self.operator.apply("first", ["101"])
        self.assertEqual(len(self.api.writes), 1)
        self.assertTrue(self.api.home[0]["manual"])

    def test_stale_preview_and_foreign_journal_refused(self):
        p = self.operator.preview(["101"])
        self.api.home[0]["manual"] = True
        with self.assertRaises(Refused):
            self.operator.apply("first", ["101"], p["baseline_digest"])
        self.assertEqual(self.api.writes, [])
        self.apply()
        other = Operator(self.api, Journal(self.directory), **dict(SCOPE, recipe="foreign"))
        with self.assertRaises(Refused):
            other.reconcile()

    def test_v1_reads_and_list_operations_never_migrate_or_write(self):
        self.apply()
        before, writes = (self.directory / "journal.json").read_bytes(), copy.deepcopy(self.api.writes)
        for call in (lambda: self.operator.list_preview("set-members", "igor-movies", movies=["101"]),
                     lambda: self.operator.create_list("new", "second", "Second", [], "digest"),
                     lambda: self.operator.set_members("new", "igor-movies", [], "digest"),
                     lambda: self.operator.show(list="igor-movies"),
                     self.operator.downgrade_preview):
            with self.assertRaises(Refused):
                call()
        self.assertEqual(self.operator.show()["reconcile"], {"state": "clean"})
        self.assertEqual(self.operator.reconcile(), {"state": "clean"})
        self.assertEqual(len(self.operator.migrate_preview()["intent_digest"]), 64)
        self.assertEqual((self.directory / "journal.json").read_bytes(), before)
        self.assertEqual(self.api.writes, writes)

    def test_migration_round_trip_is_lossless_and_old_digests_stay_bound(self):
        self.apply()
        old = self.operator.preview(["101"])["baseline_digest"]
        before, shown = (self.directory / "journal.json").read_bytes(), self.operator.show()
        migration = self.operator.migrate_preview()
        self.assertEqual(self.operator.migrate("migrate", migration["intent_digest"]),
                         {"state": "migrated", "writes": 0})
        stored = json.loads((self.directory / "journal.json").read_text())
        self.assertEqual(stored["lists"], {"igor-movies": {"row": 2}})
        self.assertEqual(stored["done"], json.loads(before)["done"])
        self.assertEqual(self.operator.show()["revision"], shown["revision"])
        for call in (lambda: self.operator.apply("next", ["102"], old),
                     lambda: self.operator.remove("gone", old),
                     lambda: self.operator.apply("first", ["101"])):
            with self.assertRaises(Refused):
                call()
        inverse = self.operator.downgrade_preview()
        self.assertEqual(self.operator.downgrade("downgrade", inverse["intent_digest"]),
                         {"state": "downgraded", "writes": 0})
        self.assertEqual((self.directory / "journal.json").read_bytes(), before)
        self.assertEqual(self.operator.show(), shown)
        self.assertEqual(self.operator.apply("first", ["101"]), {"state": "replayed", "writes": 0})
        self.assertEqual(len(self.api.writes), 4)

    def test_version_two_alias_remove_needs_delete_digest_and_restores_baseline(self):
        before = copy.deepcopy(self.api.home)
        self.apply()
        self.operator.migrate("migrate", self.operator.migrate_preview()["intent_digest"])
        membership = self.operator.preview(["101"])["baseline_digest"]
        with self.assertRaises(Refused):
            self.operator.remove("remove", membership)
        removal = self.operator.list_preview("delete-list", "igor-movies")["intent_digest"]
        self.assertEqual(self.operator.remove("remove", removal), {"state": "deleted", "writes": 2})
        self.assertEqual((self.api.home, self.api.playlists), (before, []))
        stored = json.loads((self.directory / "journal.json").read_text())
        self.assertEqual((stored["version"], stored["lists"]), (2, {}))
        self.assertEqual(stored["current"], stored["baseline"])
        self.assertEqual(len(stored["done"]), 2)

    def owned_v2(self):
        """Migrated legacy list whose fixed-field row follows two manual rows."""
        self.api.home += [{"Id": "m3", "SectionType": "playlist", "CustomName": "Same",
                           "ParentId": "manual"}, {"Id": 4}]
        self.api.row_extra = {"Name": "server-name"}
        self.apply()
        self.operator.migrate("migrate", self.operator.migrate_preview()["intent_digest"])
        return copy.deepcopy(self.api.home)

    def move(self, key, position, list_id="igor-movies"):
        intent = self.operator.list_preview("move-row", list_id, position=position)["intent_digest"]
        return self.operator.move_row(key, list_id, position, intent)

    def journal_bytes(self):
        return (self.directory / "journal.json").read_bytes()

    def forged(self, list_id, position):
        """Exact request digest over the saved clean snapshot, bypassing preview gates."""
        return digest(dict(version=2, scope=self.operator.base,
                           snapshot=json.loads(self.journal_bytes())["current"],
                           request=dict(operation="move-row", list=list_id, position=position)))

    def moves(self):
        return [args for op, args in self.api.writes if op == "move"]

    def test_move_row_any_position_both_directions_and_same_row_inverse(self):
        start = self.owned_v2()
        row, playlists = copy.deepcopy(start[-1]), copy.deepcopy(self.api.playlists)
        stored = json.loads(self.journal_bytes())
        lists, baseline = stored["lists"], stored["baseline"]
        manual = [r for r in start if r["Id"] != 2]
        plan = [(0, [2, 1, "m3", 4]), (2, [1, "m3", 2, 4]), (3, [1, "m3", 4, 2]),
                (1, [1, 2, "m3", 4])]
        for n, (position, order) in enumerate(plan):
            with self.subTest(position=position):
                prior = self.operator.show()["lists"][0]["position"]
                self.assertEqual(self.move("move-%d" % n, position), {"state": "moved", "writes": 1})
                self.assertEqual(self.moves()[-1], {"row": 2, "position": position})
                self.assertEqual([r["Id"] for r in self.api.home], order)
                self.assertEqual(self.api.home[position], row)
                self.assertEqual([r for r in self.api.home if r["Id"] != 2], manual)
                self.assertEqual(self.api.playlists, playlists)
                stored = json.loads(self.journal_bytes())
                self.assertEqual((stored["lists"], stored["baseline"]), (lists, baseline))
                self.assertEqual((stored["current"]["home"], stored["pending"], stored["run"]),
                                 (self.api.home, None, None))
                self.assertEqual(stored["done"]["move-%d" % n],
                                 dict(operation="move-row", list="igor-movies", position=position))
                self.assertEqual(self.operator.show()["lists"][0]["position"], position)
        # Same-row inverse to the index show reported before the last move.
        self.assertEqual(prior, 3)
        self.assertEqual(self.move("inverse", prior)["writes"], 1)
        self.assertEqual(self.api.home, start)
        writes = copy.deepcopy(self.api.writes)
        self.assertEqual(self.move("noop", 3), {"state": "moved", "writes": 0})
        self.assertEqual(self.api.writes, writes)
        removal = self.operator.list_preview("delete-list", "igor-movies")["intent_digest"]
        self.assertEqual(self.operator.delete_list("delete", "igor-movies", removal)["state"], "deleted")
        self.assertEqual((self.api.home, baseline["home"]), (manual, manual))
        self.assertEqual(json.loads(self.journal_bytes())["current"], baseline)

    def test_move_row_refusals_before_journaling(self):
        self.api.home.append({"Id": "m3"})
        self.api.row_extra = {"Name": "server-name"}
        self.apply()
        before, writes = self.journal_bytes(), copy.deepcopy(self.api.writes)
        for call in (lambda: self.operator.list_preview("move-row", "igor-movies", position=0),
                     lambda: self.operator.move_row("v1", "igor-movies", 0, "digest")):
            with self.assertRaisesRegex(Refused, "Journal migration required"):
                call()
        self.assertEqual(self.journal_bytes(), before)
        self.operator.migrate("migrate", self.operator.migrate_preview()["intent_digest"])
        before = self.journal_bytes()
        cases = [(position, "Invalid row position")
                 for position in (True, False, -1, 100, "0", 0.0, None, [0])]
        cases += [(3, "Row position out of range"), (99, "Row position out of range")]
        for position, message in cases:
            with self.subTest(position=position):
                with self.assertRaisesRegex(Refused, message):
                    self.operator.list_preview("move-row", "igor-movies", position=position)
                with self.assertRaisesRegex(Refused, message):
                    self.operator.move_row("bad", "igor-movies", position,
                                           self.forged("igor-movies", position))
        for list_id, message in (("other", "Unknown list"), ("../x", "Invalid opaque ID")):
            with self.subTest(list_id=list_id):
                with self.assertRaisesRegex(Refused, message):
                    self.operator.list_preview("move-row", list_id, position=0)
                with self.assertRaisesRegex(Refused, message):
                    self.operator.move_row("bad", list_id, 0, self.forged(list_id, 0))
        for args in ({}, {"position": 0, "row": 2}):
            with self.assertRaisesRegex(Refused, "Invalid list operation"):
                self.operator.list_preview("move-row", "igor-movies", **args)
        self.assertEqual(self.journal_bytes(), before)
        # A manual reorder before preview or between preview and execute refuses.
        intent = self.operator.list_preview("move-row", "igor-movies", position=0)["intent_digest"]
        self.api.home.reverse()
        self.assertEqual(self.operator.reconcile(), {"state": "drift"})
        for call in (lambda: self.operator.list_preview("move-row", "igor-movies", position=0),
                     lambda: self.operator.move_row("drift", "igor-movies", 0, intent)):
            with self.assertRaisesRegex(Refused, "Manual drift"):
                call()
        self.assertEqual(self.journal_bytes(), before)
        self.api.home.reverse()
        detach = self.operator.list_preview("detach-row", "igor-movies")["intent_digest"]
        self.operator.detach_row("detach", "igor-movies", detach)
        before = self.journal_bytes()
        with self.assertRaisesRegex(Refused, "List detached"):
            self.operator.list_preview("move-row", "igor-movies", position=0)
        with self.assertRaisesRegex(Refused, "List detached"):
            self.operator.move_row("detached", "igor-movies", 0, self.forged("igor-movies", 0))
        self.assertEqual(self.journal_bytes(), before)
        self.assertEqual([op for op, _ in self.api.writes[len(writes):]], ["unrow"])

    def test_move_row_requires_fixed_saved_row_fields(self):
        self.apply()  # The default synthetic row carries an unknown server field.
        self.operator.migrate("migrate", self.operator.migrate_preview()["intent_digest"])
        before = self.journal_bytes()
        with self.assertRaisesRegex(Refused, "Unsupported fixed row fields"):
            self.operator.list_preview("move-row", "igor-movies", position=0)
        self.assertEqual((self.journal_bytes(), self.moves()), (before, []))

    def test_move_row_replay_key_reuse_and_digest_binding(self):
        self.owned_v2()
        top = self.operator.list_preview("move-row", "igor-movies", position=0)["intent_digest"]
        middle = self.operator.list_preview("move-row", "igor-movies", position=1)["intent_digest"]
        title = self.operator.list_preview("set-title", "igor-movies", title="T")["intent_digest"]
        before = self.journal_bytes()
        for call in (lambda: self.operator.move_row("k1", "igor-movies", 1, top),
                     lambda: self.operator.set_title("k2", "igor-movies", "T", top),
                     lambda: self.operator.detach_row("k3", "igor-movies", top),
                     lambda: self.operator.move_row("k4", "igor-movies", 0, title),
                     lambda: self.operator.move_row("k5", "igor-movies", 0, None)):
            with self.assertRaisesRegex(Refused, "Fresh preview digest required"):
                call()
        self.assertEqual((self.journal_bytes(), self.moves()), (before, []))
        self.assertEqual(self.operator.move_row("top", "igor-movies", 0, top),
                         {"state": "moved", "writes": 1})
        # The position 1 digest is bound to the snapshot before that move.
        with self.assertRaisesRegex(Refused, "Fresh preview digest required"):
            self.operator.move_row("stale", "igor-movies", 1, middle)
        writes, before = copy.deepcopy(self.api.writes), self.journal_bytes()
        self.assertEqual(self.operator.move_row("top", "igor-movies", 0),
                         {"state": "replayed", "writes": 0})
        for call in (lambda: self.operator.move_row("top", "igor-movies", 1),
                     lambda: self.operator.set_title("top", "igor-movies", "T")):
            with self.assertRaisesRegex(Refused, "Idempotency key reused"):
                call()
        self.assertEqual((self.api.writes, self.journal_bytes()), (writes, before))

    def test_move_row_lost_response_completes_only_by_exact_readback(self):
        self.owned_v2()
        self.api.fail_after = "move"
        with self.assertRaises(TimeoutError):
            self.move("lost", 0)
        self.api.fail_after = None
        before = self.journal_bytes()
        self.assertEqual(self.operator.reconcile(),
                         {"state": "observed-applied", "operation": "move", "list": "igor-movies"})
        shown = self.operator.show()
        self.assertEqual((shown["reconcile"], shown["pending"], shown["lists"][0]["position"]),
                         ({"state": "observed-applied"}, {"operation": "move", "list": "igor-movies"}, 0))
        self.assertEqual(self.journal_bytes(), before)
        self.assertEqual(self.operator.move_row("lost", "igor-movies", 0),
                         {"state": "moved", "writes": 0})
        self.assertEqual(len(self.moves()), 1)
        self.assertEqual([r["Id"] for r in self.api.home], [2, 1, "m3", 4])
        # An attempted move whose Home stays unchanged is never dispatched again.
        self.api.fail_before = "move"
        with self.assertRaises(TimeoutError):
            self.move("uncertain", 3)
        self.api.fail_before = None
        before = self.journal_bytes()
        self.assertEqual(self.operator.reconcile()["state"], "unresolved")
        self.assertEqual(self.operator.show()["reconcile"], {"state": "unresolved"})
        for call in (lambda: self.operator.move_row("uncertain", "igor-movies", 3),
                     lambda: self.operator.move_row("other", "igor-movies", 1, "x")):
            with self.assertRaises(Refused):
                call()
        self.assertEqual(self.journal_bytes(), before)
        self.assertEqual(len(self.moves()), 2)

    def test_move_row_wrong_footprint_stays_unresolved(self):
        start = self.owned_v2()
        saved, playlists = self.journal_bytes(), copy.deepcopy(self.api.playlists)
        def top(home):
            home.insert(0, home.pop(3))
        effects = {"unchanged": lambda h: None,
                   "wrong index": lambda h: h.insert(1, h.pop(3)),
                   "changed row field": lambda h: (top(h), h[0].update(CustomName="manual")),
                   "other row reordered": lambda h: (top(h), h.append(h.pop(1))),
                   "playlist changed": lambda h: (top(h), self.api.playlists[0].update(access=[]))}
        for name, effect in effects.items():
            with self.subTest(effect=name):
                self.api.home, self.api.playlists = copy.deepcopy(start), copy.deepcopy(playlists)
                with self.operator.journal.locked():
                    self.operator.journal.save(json.loads(saved))
                self.api.move_effect = effect
                count = len(self.moves())
                with self.assertRaisesRegex(Refused, "Uncertain mutation"):
                    self.move("bad", 0)
                self.api.move_effect = None
                self.assertEqual(self.operator.reconcile()["state"], "unresolved")
                with self.assertRaisesRegex(Refused, "Uncertain mutation"):
                    self.operator.move_row("bad", "igor-movies", 0)
                self.assertEqual(len(self.moves()), count + 1)
                self.assertNotIn("bad", json.loads(self.journal_bytes())["done"])

    def test_move_row_not_dispatched_clears_only_pending_and_resumes(self):
        self.owned_v2()
        def refuse(recipient, args):
            raise NotDispatched("Mutation not dispatched; resume same request")
        self.api.move_row = refuse
        with self.assertRaises(NotDispatched):
            self.move("resume", 0)
        stored = json.loads(self.journal_bytes())
        self.assertIsNone(stored["pending"])
        self.assertEqual(stored["run"], {"key": "resume", "request": dict(
            operation="move-row", list="igor-movies", position=0)})
        del self.api.move_row
        with self.assertRaisesRegex(Refused, "Another request is unfinished"):
            self.operator.move_row("other", "igor-movies", 1, "x")
        self.assertEqual(self.operator.move_row("resume", "igor-movies", 0),
                         {"state": "moved", "writes": 1})
        self.assertEqual(self.moves(), [{"row": 2, "position": 0}])

    def test_native_move_index_maps_final_normal_position_to_edit_index(self):
        # Edit block G is shown as generated rows g1 and g2 in the normal view.
        rows = {k: {"Id": k, "SectionType": "x"} for k in ("A", "g1", "g2", "X", "R", "Y", "G")}
        normal = [rows[k] for k in ("A", "g1", "g2", "X", "R", "Y")]
        edit = [rows[k] for k in ("A", "G", "X", "R", "Y")]
        for row, position, native in (("R", 0, 0), ("R", 3, 2), ("R", 4, None), ("R", 5, 4),
                                      ("A", 3, 2), ("A", 5, 4), ("Y", 4, 3), ("A", 0, None)):
            with self.subTest(row=row, position=position):
                self.assertEqual(native_move_index(normal, edit, row, position), native)
                if native is not None:
                    # Native newIndex is the final edit index after removal.
                    moved = [r for r in edit if r["Id"] != row]
                    moved.insert(native, rows[row])
                    shown = [x for r in moved for x in (["g1", "g2"] if r["Id"] == "G" else [r["Id"]])]
                    self.assertEqual(shown.index(row), position)
                    self.assertEqual([x for x in shown if x != row],
                                     [r["Id"] for r in normal if r["Id"] != row])
        typed = lambda view, value: [dict(r, Id=value) if r["Id"] == "R" else r for r in view]
        cases = [(edit, "R", 1, "Unsupported generated Home section at position"),
                 (edit, "R", 2, "Unsupported generated Home section at position"),
                 (edit, "R", 6, "Row position out of range"),
                 (edit, "R", True, "Row position out of range"),
                 ([rows[k] for k in ("A", "G", "R", "X", "Y")], "R", 0, "Home views disagree"),
                 (edit + [rows["X"]], "R", 0, "Duplicate Home IDs"),
                 ([dict(r, SectionType="y") if r["Id"] == "R" else r for r in edit], "R", 0,
                  "Saved row differs between Home views"),
                 ([r for r in edit if r["Id"] != "R"], "R", 0, "Saved row differs")]
        for n, (view, row, position, message) in enumerate(cases):
            with self.subTest(case=n):
                with self.assertRaisesRegex(Refused, message):
                    native_move_index(normal, view, row, position)
        with self.assertRaisesRegex(Refused, "Saved row differs"):
            native_move_index(typed(normal, 7), typed(edit, "7"), 7, 0)

    def test_move_row_translation_refuses_before_run_and_allows_noop(self):
        self.owned_v2()
        self.assertEqual(self.move("middle", 1)["writes"], 1)
        self.assertEqual([r["Id"] for r in self.api.home], [1, 2, "m3", 4])
        # One generated edit block is shown as the normal rows m3 and 4.
        block = lambda home: home[:2] + [{"Id": "block"}]
        before, writes = self.journal_bytes(), copy.deepcopy(self.api.writes)
        changes = [(block, 2, "Unsupported generated Home section at position"),
                   (lambda h: [h[1], h[0], {"Id": "block"}], 0, "Home views disagree on row order"),
                   (lambda h: [h[0], dict(h[1], Name="edit"), {"Id": "block"}], 0,
                    "Saved row differs between Home views")]
        for change, position, message in changes:
            self.api.edit_view = change
            with self.subTest(message=message):
                with self.assertRaisesRegex(Refused, message):
                    self.operator.list_preview("move-row", "igor-movies", position=position)
                with self.assertRaisesRegex(Refused, message):
                    self.operator.move_row("refused", "igor-movies", position,
                                           self.forged("igor-movies", position))
        self.assertEqual((self.journal_bytes(), self.api.writes), (before, writes))
        self.assertIsNone(json.loads(before)["run"])
        # Already there: valid with zero writes, although the next normal row has
        # no edit-view counterpart.
        self.api.edit_view = block
        self.assertEqual(self.move("noop", 1), {"state": "moved", "writes": 0})
        self.assertEqual(self.api.writes, writes)
        # The end of both views is always a valid target.
        self.assertEqual(self.move("end", 3), {"state": "moved", "writes": 1})
        self.assertEqual([r["Id"] for r in self.api.home], [1, "m3", 4, 2])


class Crash(Exception):
    """Simulated process death at a durable boundary; never a refusal."""


class OldCore(Operator):
    """The strict version 2 field rule of origin/main 2c01ff1, which every
    public operation applied first. Everything after it is unchanged code."""
    def _journal(self):
        d = self.journal.load()
        if type(d) is dict and d.get("version") == 2:
            require(set(d) - {"migrated_from"} == V2_FIELDS, "Foreign journal")
        return super()._journal()


HISTORY = dict(operation="set-members", list="igor-movies", movies=["101"])


def record_name(key):
    return hashlib.sha256(key.encode()).hexdigest() + ".json"


class RetirementTests(unittest.TestCase):
    """Replay archive retirement in the same journal directory, under the same lock."""
    setUp = OperatorTests.setUp
    apply = OperatorTests.apply

    def journal_bytes(self):
        return (self.directory / "journal.json").read_bytes()

    def stored(self):
        return json.loads(self.journal_bytes())

    def archive(self):
        return self.directory / "replay-archive"

    def state(self):
        """Journal bytes and every archive entry with its bytes."""
        files = ({p.name: p.read_bytes() for p in self.archive().iterdir()}
                 if self.archive().exists() else None)
        return self.journal_bytes(), files

    def migrated(self, total):
        """Migrated legacy list with total completed keys, like Igor's 25."""
        self.apply()
        self.operator.migrate("migrate", self.operator.migrate_preview()["intent_digest"])
        self.fill(total - 1)

    def fill(self, count):
        """Synthetic completed history fixture under fresh keys."""
        with self.operator.journal.locked():
            d = self.operator.journal.load()
            start = len(d["done"]) + d.get("replay_archive", {"count": 0})["count"]
            d["done"].update({"history-%d" % n: dict(HISTORY) for n in range(start, start + count)})
            self.operator.journal.save(d)

    def retired(self):
        """The old 64-key ceiling, then one new run that retires all 64 keys."""
        self.migrated(DONE_LIMIT)
        self.assertEqual(self.apply("next", ["102"])["state"], "members-set")

    def outcomes(self, keys):
        """Replay result or refusal text of each key with the history request."""
        result = {}
        for key in keys:
            try:
                result[key] = self.operator.set_members(key, "igor-movies", ["101"])
            except Refused as error:
                result[key] = str(error)
        return result

    def counts(self):
        shown = self.operator.show()
        return tuple(shown[k] for k in ("done_count", "archived_count", "completed_count",
                                        "keys_exhausted"))

    def old(self):
        journal = Journal(self.directory)
        self.addCleanup(journal.close)
        return OldCore(self.api, journal, **SCOPE)

    def forged(self, movies):
        """Exact new-request digest over the saved snapshot, bypassing preview gates."""
        return digest(dict(version=2, scope=self.operator.base, snapshot=self.stored()["current"],
                           request=dict(HISTORY, movies=movies)))

    def test_old_history_replays_at_and_past_retirement(self):
        self.migrated(DONE_LIMIT)
        history = self.stored()["done"]
        self.assertEqual(self.counts(), (64, 0, 64, True))
        expected = self.outcomes(history)
        self.assertEqual(expected["first"], "Idempotency key reused")
        self.assertEqual(expected["history-1"], {"state": "replayed", "writes": 0})
        self.assertFalse(self.archive().exists())
        writes = len(self.api.writes)
        # At the old ceiling the next request first retires every key, then runs.
        self.assertEqual(self.apply("next", ["102"]), {"state": "members-set", "writes": 2})
        self.assertEqual([op for op, _ in self.api.writes[writes:]], ["clear", "add"])
        stored = self.stored()
        self.assertEqual(stored["done"], {"next": dict(HISTORY, movies=["102"])})
        self.assertEqual(stored["replay_archive"], {"version": 1, "count": 64})
        self.assertEqual(stat.S_IMODE(self.archive().lstat().st_mode), 0o700)
        names = {record_name(k): k for k in history}
        self.assertEqual(set(os.listdir(self.archive())), set(names))
        for name, key in names.items():
            s = (self.archive() / name).lstat()
            self.assertEqual((stat.S_ISREG(s.st_mode), stat.S_IMODE(s.st_mode), s.st_nlink),
                             (True, 0o600, 1))
            self.assertEqual(json.loads((self.archive() / name).read_bytes()), dict(
                version=1, scope=self.operator.base, key=key, request=history[key]))
        self.assertEqual(self.counts(), (1, 64, 65, False))
        before, writes = self.state(), copy.deepcopy(self.api.writes)
        self.assertEqual(self.outcomes(history), expected)
        for key in history:
            with self.assertRaisesRegex(Refused, "Idempotency key reused"):
                self.operator.set_members(key, "igor-movies", ["102"])
        self.assertEqual((self.state(), self.api.writes), (before, writes))
        # A second retirement adds records and never rewrites an earlier one.
        self.fill(RETIRE_AT - 1)
        second = self.stored()["done"]
        expected.update(self.outcomes(second))
        self.assertEqual(self.apply("again", ["101"]), {"state": "members-set", "writes": 2})
        self.assertEqual(self.counts(), (1, 64 + RETIRE_AT, 65 + RETIRE_AT, False))
        files = self.state()[1]
        self.assertEqual(len(files), 64 + RETIRE_AT)
        self.assertEqual({k: files[k] for k in before[1]}, before[1])
        self.assertEqual(self.outcomes(expected), expected)

    def test_retirement_waits_for_watermark_then_fails_closed_for_old_code(self):
        self.migrated(RETIRE_AT - 1)
        self.apply("below", ["102"])
        self.assertEqual(self.counts(), (RETIRE_AT, 0, RETIRE_AT, False))
        self.assertNotIn("replay_archive", self.stored())
        self.assertFalse(self.archive().exists())
        old = self.old()
        self.assertEqual(old.show()["done_count"], RETIRE_AT)
        self.apply("at", ["101"])
        self.assertEqual(self.counts(), (1, RETIRE_AT, RETIRE_AT + 1, False))
        before, writes = self.state(), copy.deepcopy(self.api.writes)
        for call in (old.show, old.reconcile, old.downgrade_preview,
                     lambda: old.set_members("history-1", "igor-movies", ["101"]),
                     lambda: old.list_preview("set-members", "igor-movies", movies=["102"]),
                     lambda: old.set_members("old-new", "igor-movies", ["102"], self.forged(["102"]))):
            with self.assertRaisesRegex(Refused, "Foreign journal"):
                call()
        self.assertEqual((self.state(), self.api.writes), (before, writes))

    def crash(self, boundary):
        """Run one new request that dies at boundary during retirement."""
        journal = self.operator.journal
        put, save = journal.archive_put, journal.save
        def archive_put(records):
            if boundary == "before-directory" or (boundary == "before-records" and records):
                raise Crash(boundary)
            if boundary == "partial-records" and records:
                put(dict(list(records.items())[:len(records) // 2]))
                raise Crash(boundary)
            put(records)
            if boundary == "after-directory":
                raise Crash(boundary)
        def save_value(value):
            count = value.get("replay_archive", {}).get("count")
            if (boundary == "manifest-save" and count == 0) or (boundary == "removal-save" and count):
                raise Crash(boundary)
            save(value)
        journal.archive_put, journal.save = archive_put, save_value
        try:
            with self.assertRaises(Crash):
                self.apply("next", ["102"])
        finally:
            del journal.archive_put, journal.save

    def test_interrupted_retirement_at_every_durable_boundary(self):
        manifests = {"before-directory": False, "after-directory": False, "manifest-save": False,
                     "before-records": True, "partial-records": True, "removal-save": True}
        for boundary, manifest in manifests.items():
            with self.subTest(boundary=boundary):
                self.setUp()
                self.migrated(DONE_LIMIT)
                history = self.stored()["done"]
                expected = self.outcomes(history)
                writes = copy.deepcopy(self.api.writes)
                self.crash(boundary)
                stored = self.stored()
                # No run was admitted, nothing was sent and no key left done.
                self.assertEqual((stored["run"], stored["pending"], stored["done"]),
                                 (None, None, history))
                self.assertEqual(self.api.writes, writes)
                self.assertEqual("replay_archive" in stored, manifest)
                # Duplicates from the interrupted pass are not counted twice.
                self.assertEqual(self.counts(), (64, 0, 64, True))
                self.assertEqual(self.outcomes(history), expected)
                if manifest:
                    with self.assertRaisesRegex(Refused, "Foreign journal"):
                        self.old().show()
                else:
                    self.assertEqual(self.old().show()["done_count"], 64)
                stray = self.archive() / (".archive-" + "0" * 32)
                if boundary == "before-records":
                    stray.write_bytes(b"{")
                    stray.chmod(0o600)
                    self.assertEqual(self.counts(), (64, 0, 64, True))
                self.assertEqual(self.apply("next", ["102"]), {"state": "members-set", "writes": 2})
                self.assertEqual(self.counts(), (1, 64, 65, False))
                self.assertEqual(set(os.listdir(self.archive())), {record_name(k) for k in history})
                self.assertEqual(self.outcomes(history), expected)

    def test_unfinished_run_is_never_archived_and_recovers_without_archive(self):
        self.migrated(DONE_LIMIT)
        self.api.fail_after = "add"
        with self.assertRaises(TimeoutError):
            self.apply("lost", ["102"])
        self.api.fail_after = None
        stored = self.stored()
        self.assertEqual((stored["run"]["key"], stored["pending"]["operation"]), ("lost", "add"))
        self.assertEqual((stored["done"], stored["replay_archive"]["count"]), ({}, 64))
        self.assertNotIn(record_name("lost"), os.listdir(self.archive()))
        before, writes = self.state(), copy.deepcopy(self.api.writes)
        with self.assertRaisesRegex(Refused, "Another request is unfinished"):
            self.operator.set_members("other", "igor-movies", ["101"], self.forged(["101"]))
        self.assertEqual((self.state(), self.api.writes), (before, writes))
        # Exact recovery of the run never reads the archive.
        self.archive().rename(self.directory / "moved")
        self.assertEqual(self.operator.set_members("lost", "igor-movies", ["102"]),
                         {"state": "members-set", "writes": 0})
        (self.directory / "moved").rename(self.archive())
        self.assertEqual(self.api.writes, writes)
        self.assertEqual(self.stored()["done"], {"lost": dict(HISTORY, movies=["102"])})
        self.assertEqual(self.counts(), (1, 64, 65, False))
        self.assertNotIn(record_name("lost"), os.listdir(self.archive()))

    def test_archive_custody_and_count_refuse_before_any_new_run(self):
        def move(name, to):
            (self.archive() / name).rename(self.directory / to)
        def write(name, value):
            path = self.archive() / name
            path.write_bytes(value if type(value) is bytes else json.dumps(value).encode())
            path.chmod(0o600)
        def manifest(value):
            with self.operator.journal.locked():
                d = self.operator.journal.load()
                if value is None:
                    del d["replay_archive"]
                else:
                    d["replay_archive"] = value
                self.operator.journal.save(d)
        def symlinked_directory():
            self.archive().rename(self.directory / "real")
            self.archive().symlink_to(self.directory / "real")
        def symlinked_record():
            move(record_name("history-1"), "real.json")
            (self.archive() / record_name("history-1")).symlink_to(self.directory / "real.json")
        foreign = dict(version=1, scope=SCOPE, key="forged", request=HISTORY)
        cases = [
            (lambda: self.archive().rename(self.directory / "moved"), "Replay archive missing"),
            (symlinked_directory, "Unsafe replay archive directory"),
            (lambda: self.archive().chmod(0o755), "Unsafe replay archive directory"),
            (lambda: write("notes.txt", b"{}"), "Unexpected replay archive entry"),
            (symlinked_record, "Unexpected replay archive entry"),
            (lambda: move(record_name("history-1"), "lost.json"), "Replay archive count mismatch"),
            (lambda: write(record_name("forged"), foreign), "Replay archive count mismatch"),
            (lambda: write(record_name("next"), dict(version=1, scope=self.operator.base, key="next",
                                                     request=HISTORY)),
             "Conflicting replay archive record"),
            (lambda: write(record_name("next"), b"{"), "Malformed replay archive record"),
            (lambda: manifest({"version": 1, "count": 63}), "Replay archive count mismatch"),
            (lambda: manifest({"version": 1, "count": -1}), "Invalid replay archive manifest"),
            (lambda: manifest({"version": 2, "count": 64}), "Invalid replay archive manifest"),
            (lambda: manifest({"version": 1, "count": 64, "pruned": 0}),
             "Invalid replay archive manifest"),
            (lambda: manifest(None), "Replay archive without manifest"),
        ]
        for n, (damage, message) in enumerate(cases):
            with self.subTest(case=n, message=message):
                self.setUp()
                self.retired()
                intent = self.forged(["101"])
                damage()
                before, writes = self.state(), copy.deepcopy(self.api.writes)
                for call in (self.operator.show, lambda: self.operator.preview(["101"]),
                             lambda: self.operator.apply("new", ["101"], intent)):
                    with self.assertRaisesRegex(Refused, message):
                        call()
                self.assertEqual((self.state(), self.api.writes), (before, writes))

    def test_archived_replay_refuses_unsafe_or_mismatched_records(self):
        name = record_name("history-1")
        def hard_link():
            os.link(self.archive() / name, self.directory / "second-link")
        def swapped():
            other = record_name("history-2")
            (self.archive() / name).rename(self.directory / "swap")
            (self.archive() / other).rename(self.archive() / name)
            (self.directory / "swap").rename(self.archive() / other)
        cases = [
            (lambda: (self.archive() / name).write_bytes(b"{"), "Malformed replay archive record"),
            (lambda: (self.archive() / name).write_bytes(b"[]"), "Malformed replay archive record"),
            (lambda: (self.archive() / name).chmod(0o644), "Unsafe replay archive record"),
            (hard_link, "Unsafe replay archive record"),
            (swapped, "Malformed replay archive record"),
            (lambda: self.archive().rename(self.directory / "moved"), "Replay archive missing"),
        ]
        for n, (damage, message) in enumerate(cases):
            with self.subTest(case=n, message=message):
                self.setUp()
                self.retired()
                damage()
                before, writes = self.state(), copy.deepcopy(self.api.writes)
                with self.assertRaisesRegex(Refused, message):
                    self.operator.set_members("history-1", "igor-movies", ["101"])
                self.assertEqual((self.state(), self.api.writes), (before, writes))

    def test_journal_size_guard_never_replaces_and_admission_reserves_headroom(self):
        self.apply()
        before = self.journal_bytes()
        with self.operator.journal.locked():
            with self.assertRaisesRegex(Refused, "Journal size limit reached"):
                self.operator.journal.save(dict(json.loads(before), padding="x" * (4 * 1024 * 1024)))
        self.assertEqual(self.journal_bytes(), before)
        self.assertEqual([p.name for p in self.directory.iterdir() if p.name.startswith(".")], [])
        # A snapshot too large for the later saves of a run refuses before any write.
        for size, admitted in ((1_400_000, False), (500_000, True)):
            with self.subTest(size=size):
                self.setUp()
                self.api.home[0]["Unknown"] = {"preserve": ["x" * size]}
                if admitted:
                    self.assertEqual(self.apply()["writes"], 4)
                    self.assertLess(len(self.journal_bytes()), 4 * 1024 * 1024)
                else:
                    with self.assertRaisesRegex(Refused, "Journal size limit reached"):
                        self.apply()
                    self.assertFalse((self.directory / "journal.json").exists())
                    self.assertEqual(self.api.writes, [])

    def test_archive_forbids_downgrade_and_reads_stay_read_only(self):
        self.retired()
        before, writes = self.state(), copy.deepcopy(self.api.writes)
        for call in (self.operator.downgrade_preview,
                     lambda: self.operator.downgrade("down", "0" * 64)):
            with self.assertRaisesRegex(Refused, "Replay archive prevents downgrade"):
                call()
        first = self.operator.show()
        self.assertEqual(self.operator.show(), first)
        self.assertEqual(self.operator.reconcile(), {"state": "clean"})
        self.operator.preview(["101"])
        self.assertEqual((self.state(), self.api.writes), (before, writes))
        # A no-op request still completes one key with zero writes.
        self.assertEqual(self.apply("noop", ["102"]), {"state": "members-set", "writes": 0})
        self.assertEqual(self.counts(), (2, 64, 66, False))

    def test_version_1_ceiling_is_unchanged_and_never_archives(self):
        self.apply()
        with self.operator.journal.locked():
            d = self.operator.journal.load()
            d["done"].update({"history-%d" % n: dict(movies=["101"], remove=False)
                              for n in range(1, DONE_LIMIT)})
            self.operator.journal.save(d)
        before = self.journal_bytes()
        with self.assertRaisesRegex(Refused, "Journal request limit reached"):
            self.apply("next", ["102"])
        self.assertEqual(self.operator.apply("history-1", ["101"]), {"state": "replayed", "writes": 0})
        self.assertEqual((self.journal_bytes(), len(self.api.writes)), (before, 4))
        self.assertFalse(self.archive().exists())


if __name__ == "__main__":
    unittest.main()
