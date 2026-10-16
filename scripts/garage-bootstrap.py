#!/usr/bin/env python3
"""Converge Garage's cluster state to stacks/objectstore/bootstrap.json.

Runs as root on the node that hosts the objectstore stack, from
playbooks/garage-bootstrap.yml (ansible.builtin.script), with the bootstrap
specification as a JSON argument:

    garage-bootstrap.py --check|--apply --spec-json '<json>' --s3-endpoint URL

It converges, in this order: the single node's layout role, each bucket and
its quotas, each key and its bucket permissions, and each bucket's lifecycle
rule (expiry and abandoned multipart uploads). Administration goes through
`garage json-api` inside the running container, over Garage's RPC, so no
admin token exists. Lifecycle rules have no admin endpoint and are set over
the S3 API on --s3-endpoint, signed with the key the specification names as
lifecycle_key; that key's secret is read from Garage into memory for the
request and is never printed or written anywhere.

Nothing is ever deleted. A key or bucket that exists in Garage but not in the
specification is reported as unmanaged and left alone; a permission granted
beyond the specification is revoked, so the specification stays the whole
truth about the keys it names.

Prints one JSON object and nothing else: changed, check, actions (what was
done, or with --check what a real run would do) and unmanaged.
"""

import argparse
import base64
import datetime
import hashlib
import hmac
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

PERMISSIONS = ("read", "write", "owner")
S3_NS = "http://s3.amazonaws.com/doc/2006-03-01/"
RULE_ID = "chaldea-retention"


class BootstrapError(Exception):
    pass


class Garage:
    """The admin API of the Garage node in one container, through its CLI."""

    def __init__(self, container):
        self.container = container

    def __call__(self, endpoint, payload=None):
        proc = subprocess.run(
            ["docker", "exec", "-i", self.container, "/garage", "json-api", endpoint, "-"],
            input=json.dumps(payload), capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            # The CLI's log lines go to stderr too; the error is the last line.
            lines = [line for line in proc.stderr.splitlines() if line.strip()]
            raise BootstrapError("%s failed: %s" % (endpoint, lines[-1] if lines else proc.returncode))
        return json.loads(proc.stdout) if proc.stdout.strip() else None


def find_container(service):
    proc = subprocess.run(
        ["docker", "ps", "-q", "--filter", "label=com.docker.swarm.service.name=" + service],
        capture_output=True, text=True, check=True)
    ids = proc.stdout.split()
    if len(ids) != 1:
        raise BootstrapError("expected one running %s container, found %d" % (service, len(ids)))
    return ids[0]


def sign_v4(method, url, headers, payload, access_key, secret_key, region, now):
    """Return headers with AWS Signature Version 4 for an S3 request."""
    parsed = urllib.parse.urlsplit(url)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()
    headers = dict(headers)
    headers.update({"host": parsed.netloc, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash})
    query = sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    canonical_query = "&".join("%s=%s" % (urllib.parse.quote(k, safe="-_.~"), urllib.parse.quote(v, safe="-_.~"))
                               for k, v in query)
    lowered = sorted((k.lower(), str(v).strip()) for k, v in headers.items())
    canonical_headers = "".join("%s:%s\n" % item for item in lowered)
    signed_headers = ";".join(k for k, _ in lowered)
    canonical_request = "\n".join([
        method, urllib.parse.quote(parsed.path or "/", safe="/-_.~"), canonical_query,
        canonical_headers, signed_headers, payload_hash])
    scope = "%s/%s/s3/aws4_request" % (datestamp, region)
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    key = ("AWS4" + secret_key).encode()
    for part in (datestamp, region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (
        access_key, scope, signed_headers, signature)
    return headers


class S3:
    """Just enough of the S3 API for bucket lifecycle configuration."""

    def __init__(self, endpoint, access_key, secret_key, region="garage"):
        self.endpoint = endpoint.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region

    def request(self, method, bucket, body=b"", headers=None):
        url = "%s/%s?lifecycle" % (self.endpoint, bucket)
        signed = sign_v4(method, url, headers or {}, body, self.access_key, self.secret_key,
                         self.region, datetime.datetime.now(datetime.timezone.utc))
        signed.pop("host")
        req = urllib.request.Request(url, data=body if method == "PUT" else None, headers=signed, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def get_lifecycle(self, bucket):
        status, body = self.request("GET", bucket)
        if status == 404:
            return []
        if status != 200:
            raise BootstrapError("GET lifecycle of %s answered %d" % (bucket, status))
        return parse_lifecycle(body)

    def put_lifecycle(self, bucket, body):
        md5 = base64.b64encode(hashlib.md5(body).digest()).decode()
        status, answer = self.request("PUT", bucket, body, {"content-md5": md5, "content-type": "application/xml"})
        if status != 200:
            raise BootstrapError("PUT lifecycle of %s answered %d: %s" % (bucket, status, answer[:200]))


def lifecycle_xml(bucket):
    """The one rule every bucket carries: expire objects, abort stale uploads."""
    return (
        '<LifecycleConfiguration xmlns="%s"><Rule><ID>%s</ID><Status>Enabled</Status>'
        "<Filter><Prefix></Prefix></Filter>"
        "<Expiration><Days>%d</Days></Expiration>"
        "<AbortIncompleteMultipartUpload><DaysAfterInitiation>%d</DaysAfterInitiation>"
        "</AbortIncompleteMultipartUpload></Rule></LifecycleConfiguration>"
        % (S3_NS, RULE_ID, bucket["expire_days"], bucket["abort_multipart_days"])).encode()


def parse_lifecycle(body):
    """Reduce a lifecycle document to comparable (id, status, prefix, days, abort days) tuples."""
    def text(node, path):
        found = node.find(path)
        return (found.text or "").strip() if found is not None else ""

    root = ET.fromstring(body)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return sorted(
        (text(rule, "ID"), text(rule, "Status"), text(rule, "Filter/Prefix") or text(rule, "Prefix"),
         text(rule, "Expiration/Days"), text(rule, "AbortIncompleteMultipartUpload/DaysAfterInitiation"))
        for rule in root.findall("Rule"))


def converge(api, spec, apply, s3_factory):
    actions, unmanaged = [], []

    # The layout: one node, one role. A change someone else has staged is
    # theirs to apply or revert; refuse rather than apply it with ours.
    layout = api("GetClusterLayout")
    if layout.get("stagedRoleChanges"):
        raise BootstrapError("the cluster layout has staged changes; apply or revert them first")
    nodes = api("GetClusterStatus")["nodes"]
    if len(nodes) != 1:
        raise BootstrapError("expected one Garage node, found %d" % len(nodes))
    node = nodes[0]["id"]
    want_role = {"zone": spec["layout"]["zone"], "capacity": spec["layout"]["capacity"]}
    role = next((r for r in layout["roles"] if r["id"] == node), None)
    # Until the node has a role Garage answers every bucket or key listing
    # with "Layout not ready"; a check run on a fresh node reads them as empty.
    readable = role is not None or apply
    if role is None or {k: role.get(k) for k in want_role} != want_role:
        actions.append("layout: zone %(zone)s, capacity %(capacity)d bytes" % want_role)
        if apply:
            api("UpdateClusterLayout", {"roles": [dict(want_role, id=node, tags=[])]})
            api("ApplyClusterLayout", {"version": layout["version"] + 1})

    # Buckets and their quotas.
    bucket_ids = {}
    for listed in api("ListBuckets") if readable else []:
        for alias in listed["globalAliases"]:
            bucket_ids[alias] = listed["id"]
    wanted_buckets = {b["name"] for b in spec["buckets"]}
    unmanaged += sorted("bucket " + alias for alias in bucket_ids if alias not in wanted_buckets)
    for bucket in spec["buckets"]:
        name = bucket["name"]
        want = {"maxSize": bucket["max_size"], "maxObjects": bucket["max_objects"]}
        info = None
        if name not in bucket_ids:
            actions.append("bucket %s: create" % name)
            if apply:
                info = api("CreateBucket", {"globalAlias": name})
                bucket_ids[name] = info["id"]
        else:
            info = api("GetBucketInfo", {"id": bucket_ids[name]})
        if info is None or info.get("quotas") != want:
            actions.append("bucket %s: quotas max_size=%d max_objects=%d" % (name, want["maxSize"], want["maxObjects"]))
            if apply:
                api("UpdateBucket", {"id": bucket_ids[name], "body": {"quotas": want}})

    # Keys and their permissions. CreateKey's answer carries the new secret;
    # only the access key ID is kept from it.
    key_ids = {}
    for listed in api("ListKeys") if readable else []:
        if listed["name"] in key_ids:
            raise BootstrapError("two keys are named %s; names must be unique" % listed["name"])
        key_ids[listed["name"]] = listed["id"]
    wanted_keys = {k["name"] for k in spec["keys"]}
    unmanaged += sorted("key " + name for name in key_ids if name not in wanted_keys)
    for key in spec["keys"]:
        name = key["name"]
        current = {}
        if name not in key_ids:
            actions.append("key %s: create" % name)
            if apply:
                key_ids[name] = api("CreateKey", {"name": name})["accessKeyId"]
        else:
            for granted in api("GetKeyInfo", {"id": key_ids[name]})["buckets"]:
                current[granted["id"]] = granted["permissions"]
        for bucket_name, perms in sorted(key["buckets"].items()):
            want = {p: p in perms for p in PERMISSIONS}
            have = current.get(bucket_ids.get(bucket_name), {})
            grant = {p: want[p] and not have.get(p, False) for p in PERMISSIONS}
            revoke = {p: have.get(p, False) and not want[p] for p in PERMISSIONS}
            for endpoint, change, verb in (("AllowBucketKey", grant, "allow"), ("DenyBucketKey", revoke, "deny")):
                if any(change.values()):
                    actions.append("key %s on %s: %s %s" % (
                        name, bucket_name, verb, ",".join(p for p in PERMISSIONS if change[p])))
                    if apply:
                        api(endpoint, {"bucketId": bucket_ids[bucket_name], "accessKeyId": key_ids[name],
                                       "permissions": change})
        for bucket_name in sorted(set(bucket_ids) - set(key["buckets"])):
            if any(current.get(bucket_ids[bucket_name], {}).values()):
                unmanaged.append("key %s on bucket %s" % (name, bucket_name))

    # Lifecycle rules, over S3, signed with the lifecycle key. In a check run
    # before the key or a bucket exists there is nothing to read yet.
    lifecycle_key = spec["lifecycle_key"]
    s3 = None
    if lifecycle_key in key_ids:
        secret = api("GetKeyInfo", {"id": key_ids[lifecycle_key], "showSecretKey": True})["secretAccessKey"]
        s3 = s3_factory(key_ids[lifecycle_key], secret)
        del secret
    for bucket in spec["buckets"]:
        name = bucket["name"]
        body = lifecycle_xml(bucket)
        if s3 is not None and name in bucket_ids and s3.get_lifecycle(name) == parse_lifecycle(body):
            continue
        actions.append("bucket %s: lifecycle expire after %d days, abort uploads after %d days" % (
            name, bucket["expire_days"], bucket["abort_multipart_days"]))
        if apply:
            if s3 is None:
                raise BootstrapError("lifecycle_key %s is not one of the specification's keys" % lifecycle_key)
            s3.put_lifecycle(name, body)

    return {"changed": apply and bool(actions), "check": not apply, "actions": actions, "unmanaged": unmanaged}


def main(argv=None):
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--spec-json", required=True)
    parser.add_argument("--s3-endpoint", required=True)
    parser.add_argument("--service", default="objectstore_garage")
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec_json)
        api = Garage(find_container(args.service))
        result = converge(api, spec, args.apply,
                          lambda access, secret: S3(args.s3_endpoint, access, secret))
    except (BootstrapError, subprocess.CalledProcessError, urllib.error.URLError) as error:
        print(json.dumps({"changed": False, "failed": True, "msg": str(error)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
