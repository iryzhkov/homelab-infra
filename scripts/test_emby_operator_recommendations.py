"""Recording adapter tests. Run only in the documented Chaldea scratch HOME."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from emby_operator_recommendations import Journal, Operator, Refused, digest

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
        self.mutate("row", body, lambda: self.home.append(dict(body, Id=2, UnknownDefault="retained")))

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
        self.mutate("unrow", row, lambda: self.home.pop())

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


if __name__ == "__main__":
    unittest.main()
