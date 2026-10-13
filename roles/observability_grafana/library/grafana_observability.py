#!/usr/bin/python
"""Reconcile only the dedicated observability resources; check mode is GET-only."""
import base64
import copy
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ansible.module_utils.basic import AnsibleModule


def desired_matches(actual: Any, desired: Any) -> bool:
    """Ignore server-added defaults, while comparing every managed field."""
    if isinstance(desired, dict):
        return isinstance(actual, dict) and all(
            key in actual and desired_matches(actual[key], value)
            for key, value in desired.items()
        )
    if isinstance(desired, list):
        return isinstance(actual, list) and len(actual) == len(desired) and all(
            desired_matches(a, d) for a, d in zip(actual, desired)
        )
    return actual == desired


def replace_loki(value: Any, uid: str) -> Any:
    if isinstance(value, dict):
        return {k: replace_loki(v, uid) for k, v in value.items()}
    if isinstance(value, list):
        return [replace_loki(v, uid) for v in value]
    return uid if value == "__LOKI_UID__" else value


def main():
    module = AnsibleModule(
        argument_spec=dict(
            api_url=dict(type="str", required=True),
            username=dict(type="str", required=True),
            password=dict(type="str", required=True, no_log=True),
            metrics_url=dict(type="str", required=True),
            folder_uid=dict(type="str", required=True),
            folder_title=dict(type="str", required=True),
            dashboards=dict(type="list", elements="dict", required=True),
        ),
        supports_check_mode=True,
    )
    p = module.params
    auth = base64.b64encode((p["username"] + ":" + p["password"]).encode()).decode()
    changes = []

    def api(method: str, path: str, payload: Any = None, missing_ok: bool = False) -> Any:
        if module.check_mode and method != "GET":
            module.fail_json(msg="Internal safety guard refused a check-mode mutation")
        request = Request(
            p["api_url"].rstrip("/") + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            module.fail_json(msg="Grafana API %s %s failed (HTTP %s)" % (method, path, exc.code))
        except (URLError, ValueError, TimeoutError):
            module.fail_json(msg="Grafana API %s %s failed; check connectivity and service health" % (method, path))

    sources = api("GET", "/api/datasources")
    loki_sources = [s for s in sources if s.get("type") == "loki"]
    if len(loki_sources) != 1:
        module.fail_json(msg="Expected exactly one existing Loki datasource; refusing ambiguous provisioning")
    loki_uid = loki_sources[0]["uid"]
    desired_source = {
        "uid": "homelab-metrics", "name": "Homelab Metrics", "type": "prometheus",
        "access": "proxy", "url": p["metrics_url"], "basicAuth": False,
        "jsonData": {"httpMethod": "POST", "timeInterval": "30s"},
    }
    existing_source = next((s for s in sources if s.get("uid") == "homelab-metrics"), None)
    if existing_source and existing_source.get("type") != "prometheus":
        module.fail_json(msg="homelab-metrics UID is occupied by a different datasource type")
    if not desired_matches(existing_source, desired_source):
        changes.append("datasource homelab-metrics")
        if not module.check_mode:
            # Preserve whether the source is already the default.
            payload = dict(desired_source, isDefault=bool((existing_source or {}).get("isDefault", False)))
            if existing_source:
                api("PUT", "/api/datasources/uid/homelab-metrics", payload)
            else:
                api("POST", "/api/datasources", payload)

    folder_path = "/api/folders/" + quote(p["folder_uid"], safe="")
    folder = api("GET", folder_path, missing_ok=True)
    if folder is None:
        changes.append("folder " + p["folder_uid"])
        if not module.check_mode:
            api("POST", "/api/folders", {"uid": p["folder_uid"], "title": p["folder_title"]})
    elif folder["title"] != p["folder_title"]:
        changes.append("folder title")
        if not module.check_mode:
            api("PUT", folder_path, {"title": p["folder_title"], "version": folder.get("version", 0)})

    for document in p["dashboards"]:
        desired = replace_loki(copy.deepcopy(document), loki_uid)
        uid = desired.get("uid", "")
        if not uid.startswith("homelab-observe-"):
            module.fail_json(msg="Refusing a dashboard outside the owned UID namespace")
        existing = api("GET", "/api/dashboards/uid/" + quote(uid, safe=""), missing_ok=True)
        actual = existing["dashboard"] if existing else None
        folder_matches = existing and existing.get("meta", {}).get("folderUid") == p["folder_uid"]
        if not desired_matches(actual, desired) or not folder_matches:
            changes.append("dashboard " + uid)
            if not module.check_mode:
                payload = copy.deepcopy(desired)
                payload["id"] = actual.get("id") if actual else None
                payload["version"] = actual.get("version", 0) if actual else 0
                api("POST", "/api/dashboards/db", {
                    "dashboard": payload, "folderUid": p["folder_uid"],
                    "overwrite": True, "message": "Reconcile Ansible observability source",
                })

    readiness = "pending datasource creation (check mode)"
    if existing_source or not module.check_mode:
        health = api("GET", "/api/datasources/uid/homelab-metrics/health")
        if str(health.get("status", "")).lower() not in ("ok", "success"):
            module.fail_json(msg="VictoriaMetrics datasource health check failed", changed=bool(changes))
        query = api("GET", "/api/datasources/proxy/uid/homelab-metrics/api/v1/query?" + urlencode({"query": "up"}))
        if query.get("status") != "success":
            module.fail_json(msg="VictoriaMetrics representative query failed", changed=bool(changes))
        readiness = "healthy; up query returned %d series" % len(query.get("data", {}).get("result", []))
    module.exit_json(
        changed=bool(changes), changes=changes, readiness=readiness,
        summary=("Would reconcile " if module.check_mode else "Reconciled ") +
        str(len(changes)) + " observability resources; " + readiness,
    )


if __name__ == "__main__":
    main()
