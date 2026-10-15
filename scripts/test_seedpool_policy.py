#!/usr/bin/env python3
import copy
import unittest
from seedpool_policy import snapshot, retirement_plan, FLOOR_BYTES, MINIMUM_SEED_SECONDS

TIB = 1024**4


def torrent(n, total_bytes, **changes):
    result = {"hash": format(n, "040x"), "tracker": "https://tracker.seedpool.org/announce/PRIVATE",
              "state": "stalledUP", "progress": 1, "amount_left": 0,
              "size": total_bytes, "total_size": total_bytes, "seeding_time": MINIMUM_SEED_SECONDS}
    result.update(changes)
    return result


class SeedPolicyTests(unittest.TestCase):
    def test_binary_floor_and_current_below_floor(self):
        self.assertEqual(FLOOR_BYTES, 21990232555520)
        data = [torrent(1, 21595838435688)]
        state = snapshot(data)
        self.assertEqual(state["headroom_bytes"], 0)
        self.assertFalse(retirement_plan(data, [data[0]["hash"]])["allowed"])

    def test_active_complete_full_selected_only(self):
        rows = [torrent(1, TIB), torrent(2, TIB, state="stoppedUP"),
                torrent(3, TIB, state="queuedUP"), torrent(4, TIB, state="checkingUP"),
                torrent(5, TIB, size=TIB // 2), torrent(6, TIB, progress=.5),
                torrent(7, TIB, tracker="https://seedpool.org.evil.invalid/announce/secret")]
        state = snapshot(rows)
        self.assertEqual(state["current_active_bytes"], TIB)
        self.assertEqual(state["active_count"], 1)

    def test_ten_days_is_seeding_time_not_download_age_or_ratio(self):
        old = torrent(1, 25*TIB, seeding_time=MINIMUM_SEED_SECONDS-1,
                      added_on=0, ratio=999)
        state = snapshot([old])
        self.assertEqual(state["below_minimum_count"], 1)
        decision = retirement_plan([old, torrent(2, 25*TIB)], [old["hash"]])
        self.assertIn("minimum_seed_age_not_met", decision["blocked_reasons"])

    def test_whole_batch_floor_not_independent_per_torrent(self):
        rows = [torrent(1, 20*TIB), torrent(2, 4*TIB), torrent(3, 4*TIB)]
        self.assertTrue(retirement_plan(rows, [rows[1]["hash"]])["allowed"])
        self.assertTrue(retirement_plan(rows, [rows[1]["hash"], rows[2]["hash"]])["allowed"])
        bad = retirement_plan(rows, [rows[0]["hash"], rows[1]["hash"]])
        self.assertFalse(bad["allowed"])
        self.assertEqual(bad["projected_active_bytes"], 4*TIB)

    def test_unknown_missing_duplicate_and_partial_fail_closed(self):
        valid = torrent(1, 25*TIB)
        unknown = torrent(2, TIB, tracker="")
        self.assertFalse(retirement_plan([valid, unknown], [valid["hash"]])["allowed"])
        self.assertFalse(retirement_plan([valid], ["f"*40])["allowed"])
        self.assertFalse(retirement_plan([valid], [valid["hash"], valid["hash"]])["allowed"])
        self.assertFalse(retirement_plan([valid], [])["allowed"])
        self.assertFalse(retirement_plan([valid, valid], [valid["hash"]])["allowed"])
        partial = torrent(3, TIB, size=1)
        self.assertFalse(retirement_plan([valid, partial], [partial["hash"]])["allowed"])

    def test_nonfinite_boolean_and_malformed_data_fail_closed(self):
        for bad in [True, float("nan"), float("inf"), -1, "123"]:
            state = snapshot([torrent(1, TIB, seeding_time=bad)])
            self.assertEqual(state["unknown_count"], 1)
        state = snapshot([None, torrent(1, TIB, tracker="http://[broken")])
        self.assertEqual(state["unknown_count"], 2)

    def test_no_speculative_credit_and_input_unchanged(self):
        rows = [torrent(1, 22*TIB), torrent(2, 4*TIB, state="downloading", progress=.9)]
        before = copy.deepcopy(rows)
        self.assertFalse(retirement_plan(rows, [rows[0]["hash"]])["allowed"])
        self.assertEqual(rows, before)


if __name__ == "__main__":
    unittest.main()
