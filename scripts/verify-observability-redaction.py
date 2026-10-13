#!/usr/bin/env python3
"""Bounded live redaction acceptance with dummy values; never reads credentials."""
import argparse, json, shlex, subprocess, time, urllib.parse, urllib.request, uuid

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ssh-host", default="homelab")
    p.add_argument("--host-label", default="homelab")
    p.add_argument("--loki-url", default="http://192.168.70.223:3100")
    p.add_argument("--job", default="redaction-validation")
    a = p.parse_args()
    nonce = "OBS_REDACT_" + uuid.uuid4().hex
    sentinels = ["OBSFAKE" + str(i) + uuid.uuid4().hex for i in range(9)]
    rows = [
        "Authorization: Bearer " + sentinels[0],
        "Authorization: Basic " + sentinels[1],
        "https://example.invalid/?token=" + sentinels[2] + "&a=b",
        "api_key=" + sentinels[3],
        "secret: " + sentinels[4],
        '{"password":"' + sentinels[5] + '"}',
        "postgres://user:" + sentinels[6] + "@database/db",
        "ghp_" + sentinels[7],
        "eyJ" + sentinels[8] + ".OBSFAKEsignature.OBSFAKEtail",
    ]
    body = "\n".join(nonce + " case=" + str(i) + " " + row for i, row in enumerate(rows)) + "\n"
    if a.job == "redaction-validation":
        cmd = ["ssh", "-o", "BatchMode=yes", a.ssh_host, "sudo -n tee -a /var/lib/alloy/redaction-test.log >/dev/null"]
    elif a.job == "journal":
        cmd = ["ssh", "-o", "BatchMode=yes", a.ssh_host, "logger -t observability-redaction"]
    elif a.job == "docker":
        name = "observability-redaction-" + uuid.uuid4().hex[:8]
        script = "sleep 15" + chr(10) + "printf \'%s\' " + shlex.quote(body) + chr(10) + "sleep 40"
        image = "nginx@sha256:8f84ed99befc3891b8f329c5c202785278a2cfb7c25107d57fb2a134a3117433"
        command = "docker run --rm -d --name " + name + " --log-driver local " + image + " sh -c " + shlex.quote(script)
        cmd = ["ssh", "-o", "BatchMode=yes", a.ssh_host, command]
    else:
        raise SystemExit("unsupported acceptance source")
    subprocess.run(cmd, input=body if a.job != "docker" else None, text=True, check=True, capture_output=True)
    query = '{host="' + a.host_label + '",job="' + a.job + '"} |= "' + nonce + '"'
    url = a.loki_url.rstrip("/") + "/loki/api/v1/query_range?" + urllib.parse.urlencode({
        "query": query, "start": str(time.time_ns() - 120_000_000_000), "limit": "100"})
    deadline = time.monotonic() + 45
    while True:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.load(response)
        lines = [v[1] for s in data.get("data", {}).get("result", []) for v in s["values"] if v[1].startswith(nonce + " case=")]
        if any(s in line for s in sentinels for line in lines):
            raise SystemExit("FAIL: a dummy secret reached Loki unredacted")
        if len(lines) >= len(rows):
            if sum("[REDACTED]" in line for line in lines) < len(rows):
                raise SystemExit("FAIL: not every test case was redacted")
            print("PASS: " + a.job + " redacted all " + str(len(rows)) + " dummy cases before ingest")
            return
        if time.monotonic() >= deadline:
            raise SystemExit("FAIL: dummy input did not arrive within the acceptance deadline")
        time.sleep(2)

if __name__ == "__main__":
    main()
