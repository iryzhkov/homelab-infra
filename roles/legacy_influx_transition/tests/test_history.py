#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("proof", Path(__file__).parents[1] / "files/influx-history-proof.py")
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)

class HistoryProofTest(unittest.TestCase):
    def test_table_order_and_identifier_do_not_change_historical_proof(self):
        first = "#datatype,string,long,dateTime:RFC3339,double,string\n,result,table,_time,_value,entity\n,_result,0,2026-10-12T00:00:00Z,1.0,a\n,_result,1,2026-10-12T00:00:00Z,2.0,b\n"
        second = ",result,table,_time,_value,entity\n,_result,72,2026-10-12T00:00:00Z,2.0,b\n,_result,91,2026-10-12T00:00:00Z,1.0,a\n"
        self.assertEqual(proof.canonical(first), proof.canonical(second))
        self.assertEqual(proof.canonical(first)["rows"], 2)
    def test_changed_value_or_missing_record_is_rejected_by_digest(self):
        original = ",result,table,_time,_value\n,_result,0,2026-10-12T00:00:00Z,1\n,_result,0,2026-10-12T00:01:00Z,2\n"
        self.assertNotEqual(proof.canonical(original)["sha256"], proof.canonical(original.replace(",2\n", ",3\n"))["sha256"])
        self.assertNotEqual(proof.canonical(original)["sha256"], proof.canonical(original.splitlines(True)[:-1][0] + original.splitlines(True)[1])["sha256"])
    def test_empty_success_response_is_not_history_or_fresh_write_acceptance(self):
        with self.assertRaises(ValueError):
            proof.canonical("#datatype,string,long\n,result,table,_time,_value\n")

if __name__ == "__main__":
    unittest.main()
