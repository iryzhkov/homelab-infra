"""Read-only check and idempotent home preference/bookmark reconciliation."""
import importlib.util
import io
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

MODULE_PATH = pathlib.Path(__file__).parents[1] / "library/grafana_observability.py"
class Done(Exception):
    def __init__(self, result): self.result = result

class ProvisioningTest(unittest.TestCase):
    def run_module(self, check, state):
        doc = {"uid": "homelab-observe-home", "title": "Home"}
        params = dict(api_url="http://test", username="admin", password="private",
                      metrics_url="http://metrics", folder_uid="folder", folder_title="Folder",
                      dashboards=[doc], external_shares=[], legacy_dashboard_uids=[],
                      home_dashboard_uid=doc["uid"], starred_dashboard_uids=[doc["uid"]])
        class Module:
            check_mode = check
            def __init__(self, **kwargs): self.params = params
            def fail_json(self, **kwargs): raise AssertionError(kwargs)
            def exit_json(self, **kwargs): raise Done(kwargs)
        calls = []
        def request(req, **kwargs):
            path = req.full_url.removeprefix("http://test")
            method = req.get_method()
            calls.append((method, path))
            if method == "GET":
                if path == "/api/datasources":
                    body = [{"uid":"loki", "type":"loki"}, {"uid":"homelab-metrics","name":"Homelab Metrics","type":"prometheus","access":"proxy","url":"http://metrics","basicAuth":False,"jsonData":{"httpMethod":"POST","timeInterval":"30s"}}]
                elif path == "/api/folders/folder": body = {"title":"Folder"}
                elif path == "/api/search?starred=true": body = [{"uid":doc["uid"]}] if state["starred"] else []
                elif path == "/api/org/preferences": body = state["preferences"]
                elif path.startswith("/api/dashboards/uid/"): body = {"dashboard":dict(doc,id=7), "meta":{"folderUid":"folder","isStarred":state["starred"]}}
                elif path.endswith("/health"): body = {"status":"OK"}
                elif "/api/v1/query?" in path: body = {"status":"success","data":{"result":[]}}
                else: raise AssertionError(path)
            elif method == "PUT" and path == "/api/org/preferences":
                state["preferences"] = json.loads(req.data); body = {}
            elif method == "POST" and path == "/api/user/stars/dashboard/uid/homelab-observe-home":
                state["starred"] = True; body = {}
            else: raise AssertionError((method,path))
            return io.BytesIO(json.dumps(body).encode())
        fake = types.ModuleType("ansible.module_utils.basic"); setattr(fake, "AnsibleModule", Module)
        with patch.dict(sys.modules, {"ansible.module_utils.basic":fake}):
            spec=importlib.util.spec_from_file_location("grafana_test_module", MODULE_PATH)
            assert spec is not None and spec.loader is not None
            module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with patch.object(module,"urlopen",request):
            try: module.main()
            except Done as done: return done.result,calls
        raise AssertionError("No result")
    def test_check_reports_changes_without_mutation(self):
        state={"preferences":{"theme":"dark","timezone":"browser"},"starred":False}
        result,calls=self.run_module(True,state)
        self.assertTrue(result["changed"]); self.assertTrue(all(m=="GET" for m,p in calls))
        self.assertFalse(state["starred"]); self.assertNotIn("homeDashboardUID",state["preferences"])
    def test_apply_preserves_preferences_and_second_run_is_read_only(self):
        state={"preferences":{"theme":"dark","timezone":"browser"},"starred":False}
        result,_=self.run_module(False,state)
        self.assertTrue(result["changed"]); self.assertTrue(state["starred"])
        self.assertEqual(state["preferences"]["theme"],"dark")
        self.assertEqual(state["preferences"]["homeDashboardUID"],"homelab-observe-home")
        result,calls=self.run_module(False,state)
        self.assertFalse(result["changed"]); self.assertTrue(all(m=="GET" for m,p in calls))
if __name__ == "__main__": unittest.main()
