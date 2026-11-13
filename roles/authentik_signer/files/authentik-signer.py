#!/usr/bin/python3
"""Root-only cohort signer controller. No private keys leave Authentik."""
import argparse
import base64
import copy
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ORIGIN = "https://auth.ryzhkov.dev"
ORIGINAL = "8bbd6516-af33-4e64-a73b-caa39e692c29"
STATE = Path("/var/lib/authentik-signer")
SLUGS = ("chaldea-jocasta", "jocasta-managed-interactive", "jocasta-laptop-interactive",
         "ov-http-a63e197f", "chaldea-holonet", "shizune-mcp", "ha-http-a63e197f",
         "homelab-observer", "chaldea-host-rotation", "chaldea-akasha")
FIELDS = ("name", "client_id", "client_type", "grant_types", "authorization_flow",
          "invalidation_flow", "access_token_validity", "refresh_token_validity",
          "issuer_mode", "redirect_uris", "property_mappings", "encryption_key",
          "sub_mode", "include_claims_in_id_token", "jwt_federation_sources",
          "jwt_federation_providers", "refresh_token_threshold")
WAIT = 600
MONTH = 30 * 86400


def require(value):
    if not value:
        raise RuntimeError("signer safety check refused")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def atomic(path, value, mode=0o600):
    raw = canonical(value)
    fd = os.open(str(path) + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    os.fchmod(fd, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(path) + ".new", path)
    d = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(d)
    finally:
        os.close(d)


def private(path):
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o600)
    return json.loads(path.read_bytes())


def jwks(keys):
    require(1 <= len(keys) <= 2)
    seen = set()
    for k in keys:
        require(set(k) == {"kid", "kty", "use", "alg", "n", "e"})
        require(k["kty"] == "RSA" and k["use"] == "sig" and k["alg"] == "RS256")
        require(isinstance(k["kid"], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", k["kid"]))
        require(k["kid"] not in seen)
        seen.add(k["kid"])
        for field in ("n", "e"):
            require(isinstance(k[field], str) and re.fullmatch(r"[A-Za-z0-9_-]+", k[field]))
            raw = base64.urlsafe_b64decode(k[field] + "=" * (-len(k[field]) % 4))
            require(raw and raw[0] != 0 and base64.urlsafe_b64encode(raw).decode().rstrip("=") == k[field])
        require(integer(k["n"]).bit_length() >= 2048 and integer(k["e"]) == 65537)
    return canonical({"keys": sorted(keys, key=lambda k: k["kid"])})


def integer(value):
    return int.from_bytes(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)), "big")


def projection(provider):
    # Never persist/hash OAuth secrets or unknown serializer fields.
    return {k: provider[k] for k in FIELDS}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("redirect refused")


class IO:
    def __init__(self, config, secrets):
        self.c, self.secrets = config, secrets
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None):
        require(path.startswith("/") and not path.startswith("//") and "private" not in path)
        data = None if body is None else canonical(body)
        request = urllib.request.Request(ORIGIN + "/api/v3" + path, data,
            {"Authorization": "Bearer " + self.secrets["authentik_api_token"],
             "Content-Type": "application/json"}, method=method)
        with self.opener.open(request, timeout=30) as response:
            raw = response.read(4 * 1024 * 1024)
            return json.loads(raw) if raw else None

    def authority(self):
        actor = self.request("GET", "/core/users/me/")
        require(actor["user"]["is_superuser"] is True)

    def rows(self, path):
        out, page, count = [], 1, None
        while True:
            data = self.request("GET", path + ("&" if "?" in path else "?") + "superuser_full_list=true&page_size=100&page=" + str(page))
            seen_count = data["pagination"]["count"]
            require(type(seen_count) is int and seen_count >= 0
                    and (count is None or seen_count == count))
            count = seen_count
            out.extend(data["results"])
            require(len(out) <= count)
            if not data["pagination"]["next"]:
                require(len(out) == count and len({r["pk"] for r in out}) == len(out))
                return out
            page += 1
            require(page < 1000)

    def public(self, url):
        require(url.startswith("https://"))
        with self.opener.open(url, timeout=30) as response:
            require(response.status == 200)
            return response.read(65536)

    def key(self, pk, staging=False):
        meta = self.request("GET", "/crypto/certificatekeypairs/" + pk + "/")
        require(meta["key_type"] == "rsa" and meta["private_key_available"])
        if staging:
            self.patch(self.c["stage"]["provider"], pk)
            actual = self.request("GET", "/providers/oauth2/" + str(self.c["stage"]["provider"]) + "/")
            require(actual["pk"] == self.c["stage"]["provider"]
                    and projection(actual) == self.c["stage"]["body"] and actual["signing_key"] == pk)
            slug = self.c["stage"]["slug"]
        else:
            slug = self.c["providers"][0]["slug"]
        keys = json.loads(self.public(ORIGIN + "/application/o/" + slug + "/jwks/"))["keys"]
        require(len(keys) == 1)
        require(set(keys[0]) <= {"kid", "kty", "use", "alg", "n", "e", "x5c", "x5t", "x5t#S256"})
        key = {k: keys[0][k] for k in ("kid", "kty", "use", "alg", "n", "e")}
        jwks([key])
        from cryptography.x509 import load_pem_x509_certificate
        cert = self.request("GET", "/crypto/certificatekeypairs/" + pk + "/view_certificate/")["data"]
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
        certificate = load_pem_x509_certificate(cert.encode())
        public_key = certificate.public_key()
        require(isinstance(public_key, RSAPublicKey))
        numbers = public_key.public_numbers()
        require(numbers.n == integer(key["n"]) and numbers.e == integer(key["e"]))
        if staging:
            require(numbers.n.bit_length() == 4096)
            remaining = (certificate.not_valid_after_utc - dt.datetime.now(dt.timezone.utc)).total_seconds()
            require(remaining >= 60 * 86400)
        return key

    def patch(self, pk, key):
        # Deliberately never send expires, grants, ownership, or serializer defaults.
        return self.request("PATCH", "/providers/oauth2/" + str(pk) + "/", {"signing_key": key})

    def publish(self, keys):
        raw = jwks(keys)
        # The receiving directory is root-owned and mounted read-only into nginx.
        remote = ("import os,sys; p='/srv/swarm/observability/jwks'; "
                  "b=sys.stdin.buffer.read(65536); "
                  "f=open(p+'/cohort.json.new','wb'); f.write(b); f.flush(); "
                  "os.fsync(f.fileno()); f.close(); os.chmod(p+'/cohort.json.new',0o644); "
                  "os.replace(p+'/cohort.json.new',p+'/cohort.json'); "
                  "d=os.open(p,os.O_DIRECTORY); os.fsync(d); os.close(d)")
        subprocess.run(["runuser", "-u", "igor", "--", "ssh", "-oBatchMode=yes", "homelab", "sudo", "-n", "python3", "-c",
                        shlex.quote(remote)], input=raw, check=True, capture_output=True, timeout=60)

    def edges(self, keys):
        raw = jwks(keys)
        # URLs must identify each edge through independent TLS-verified DNS names
        # with an existing auth hostname certificate. curl --resolve preserves SNI.
        for edge in self.c["edges"]:
            require(re.fullmatch(r"[0-9a-fA-F:.]+", edge))
            for p in self.c["providers"]:
                url = ORIGIN + "/application/o/" + p["slug"] + "/jwks/"
                result = subprocess.run(["curl", "--silent", "--show-error", "--fail",
                    "--noproxy", "*", "--max-time", "20", "--resolve",
                    "auth.ryzhkov.dev:443:" + edge, url], check=True, capture_output=True)
                require(result.stdout == raw)

    def clock(self):
        result = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                                check=True, capture_output=True, timeout=10)
        require(result.stdout.strip() == b"yes")

    def check_canaries(self):
        forms = self.secrets.get("signer_canaries", {})
        eligible = {p["slug"] for p in self.c["providers"]
                    if "client_credentials" in p["body"]["grant_types"]}
        require(set(forms) == eligible)
        require(all(f["grant_type"] == "client_credentials" for f in forms.values()))

    def probe(self, expected, keys):
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives import hashes
        self.check_canaries()
        by_kid = {k["kid"]: k for k in keys}
        for p in self.c["providers"]:
            if "client_credentials" not in p["body"]["grant_types"]:
                continue
            for replica in self.c["replicas"]:
                form = self.secrets["signer_canaries"][p["slug"]]
                require(form["grant_type"] == "client_credentials")
                # Credentials stay in-process, never argv, files, journal or output.
                url = replica + "/application/o/token/"
                require(replica.startswith("https://") and "@" not in replica)
                request = urllib.request.Request(url, urllib.parse.urlencode(form).encode())
                with self.opener.open(request, timeout=30) as response:
                    token = json.load(response)["access_token"]
                header, payload, signature = token.split(".")
                decode = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
                h, claims = json.loads(decode(header)), json.loads(decode(payload))
                require(h["alg"] == "RS256" and h["kid"] == expected["kid"])
                k = by_kid[h["kid"]]
                rsa.RSAPublicNumbers(integer(k["e"]), integer(k["n"])).public_key().verify(
                    decode(signature), (header + "." + payload).encode(), padding.PKCS1v15(), hashes.SHA256())
                now = time.time()
                require(claims["iss"] == ORIGIN + "/application/o/" + p["slug"] + "/")
                require(claims["aud"] in (p["canary_audience"], [p["canary_audience"]]))
                require(0 < claims["exp"] - claims["iat"] <= 300 and claims["exp"] > now
                        and abs(claims["iat"] - now) <= 60)

    def alert(self):
        return subprocess.run(["runuser", "-u", "igor", "--", "feed", "post", "--source", "feed", "--severity", "fail",
             "Authentik cohort signer rotation failed; old signer/overlap retained. Inspect root journal."],
             capture_output=True, timeout=30).returncode == 0


class Controller:
    def __init__(self, io, state, save, now=time.time, sleep=time.sleep):
        self.io, self.s, self.save, self.now, self.sleep = io, state, save, now, sleep

    def commit(self, **fields):
        self.s.update(fields)
        self.save(self.s)

    def preflight(self):
        self.io.authority()
        c = self.io.c
        require(type(c["cache_ceiling"]) is int and 0 < c["cache_ceiling"] <= 300
                and type(c["max_inflight"]) is int and 0 <= c["max_inflight"] <= 60)
        require(len(c["edges"]) == 2 and len(set(c["edges"])) == 2 and len(c["replicas"]) == 1)
        self.io.check_canaries()
        require(set(SLUGS[:-1]) <= {p["slug"] for p in c["providers"]} <= set(SLUGS))
        require(len({p["pk"] for p in c["providers"]}) == len(c["providers"]) > 0)
        require(self.s["contracts"] == c["providers"])
        self.io.clock()
        allowed = {self.s["current"]["pk"]}
        if self.s.get("next"):
            allowed.add(self.s["next"]["pk"])
        if self.s.get("retiring"):
            allowed.add(self.s["retiring"]["pk"])
        for p in c["providers"]:
            row = self.io.request("GET", "/providers/oauth2/" + str(p["pk"]) + "/")
            require(row["pk"] == p["pk"] and projection(row) == p["body"] and row["signing_key"] in allowed)
            if self.s["phase"] == "retire" and row["signing_key"] != self.s["current"]["pk"]:
                self.commit(stopped=self.now() + c["max_inflight"])
                raise RuntimeError("old issuance observed")
            require(row["encryption_key"] is None and row["issuer_mode"] == "per_provider")
            require(re.fullmatch(r"seconds=([1-9][0-9]{0,2})", row["access_token_validity"]))
            require(int(row["access_token_validity"].split("=")[1]) <= 300)
            app = self.io.request("GET", "/core/applications/" + p["slug"] + "/")
            require(app["provider"] == p["pk"] and app["slug"] == p["slug"])
        stage = c["stage"]
        provider = self.io.request("GET", "/providers/oauth2/" + str(stage["provider"]) + "/")
        require(provider["pk"] == stage["provider"] and projection(provider) == stage["body"] and provider["grant_types"] == ["authorization_code"] and provider["redirect_uris"] == []
                and provider["encryption_key"] is None)
        app = self.io.request("GET", "/core/applications/" + stage["slug"] + "/")
        require(app["pk"] == stage["application"] and app["slug"] == stage["slug"]
                and app["provider"] == stage["provider"] and app["backchannel_providers"] == []
                and app["policy_engine_mode"] == "all")
        stages = [p for p in self.io.rows("/providers/oauth2/") if p["name"] == stage["slug"]]
        require(len(stages) == 1 and stages[0]["pk"] == stage["provider"])
        apps = self.io.rows("/core/applications/")
        require([a["slug"] for a in apps if a.get("provider") == stage["provider"]] == [stage["slug"]])
        require(not any(stage["provider"] in a.get("backchannel_providers", []) for a in apps))
        policy = self.io.request("GET", "/policies/expression/" + stage["policy"] + "/")
        require(policy["expression"] == "return False" and policy["name"] == stage["policy_name"])
        bindings = [b for b in self.io.rows("/policies/bindings/") if b["target"] == app["pk"]]
        require(len(bindings) == 1 and bindings[0]["pk"] == stage["binding"]
                and bindings[0]["policy"] == stage["policy"] and bindings[0]["enabled"]
                and bindings[0]["negate"] is False and bindings[0]["group"] is None
                and bindings[0]["user"] is None)
        if self.s.get("create"):
            pending = self.s["create"]
            allowed.update(k["pk"] for k in self.io.rows("/crypto/certificatekeypairs/")
                           if k["name"] == pending["name"] and k["pk"] not in pending["before"])
        require(provider["signing_key"] in allowed)

    def keys(self):
        return [x["jwk"] for x in (self.s["current"], self.s.get("next"), self.s.get("retiring")) if x]

    def reconcile_create(self):
        pending = self.s["create"]
        rows = [k for k in self.io.rows("/crypto/certificatekeypairs/") if k["name"] == pending["name"]]
        # A lost response is never followed by a second POST. A zero-match
        # uncertain create requires operator reconciliation, not a guessed retry.
        require(len(rows) == 1)
        pk = rows[0]["pk"]
        if pending.get("pk"):
            require(pk == pending["pk"])
        else:
            require(pk not in pending["before"])
            self.commit(create=dict(pending, pk=pk))
        key = self.io.key(pk, staging=True)
        self.commit(next={"pk": pk, "jwk": key, "owned": True}, create=None, phase="publish")

    def switch(self, target, restore=False):
        drift = False
        for p in self.io.c["providers"]:
            pk = p["pk"]
            row = self.io.request("GET", "/providers/oauth2/" + str(pk) + "/")
            allowed = {x["pk"] for x in (self.s["current"], self.s.get("next"), self.s.get("retiring")) if x}
            require(row["pk"] == pk and row["signing_key"] in allowed)
            require(restore or projection(row) == p["body"])
            if row["signing_key"] != target["pk"]:
                self.io.patch(pk, target["pk"])
            row = self.io.request("GET", "/providers/oauth2/" + str(pk) + "/")
            require(row["pk"] == pk and row["signing_key"] == target["pk"])
            drift |= projection(row) != p["body"]
            require(restore or not drift)
        require(not drift)
        self.io.probe(target["jwk"], self.keys())

    def rollback(self):
        require(self.s["phase"] not in ("prune", "delete", "idle"))
        require(self.s.get("next") or self.s.get("retiring"))
        # During retirement the previous signer remains available for rollback.
        if self.s.get("retiring"):
            self.commit(next=self.s["current"], current=self.s["retiring"], retiring=None)
        self.commit(phase="rollback")
        self.switch(self.s["current"], restore=True)
        self.commit(phase="rollback_wait", stopped=self.now() + self.io.c["max_inflight"])

    def run(self, command="rotate"):
        if self.s.get("alert"):
            try:
                if self.io.alert():
                    self.commit(alert=False)
            except Exception:
                pass
        try:
            self.preflight()
            if command != "publish" and self.s["phase"] in ("idle", "create"):
                self.io.edges([self.s["current"]["jwk"]])
            if self.s["phase"] in ("wait", "switch", "switching", "retire", "rollback", "rollback_wait"):
                self.io.edges(self.keys())
            if command == "publish":
                require(self.s["phase"] == "idle")
                self.io.publish(self.keys())
                self.io.edges(self.keys())
                return
            if command == "rollback":
                self.rollback()
            phase = self.s["phase"]
            if phase == "idle":
                if self.now() - self.s["rotated"] < MONTH:
                    return
                rows = self.io.rows("/crypto/certificatekeypairs/")
                pending = {"name": "homelab-cohort-" + str(uuid.uuid4()),
                           "before": [r["pk"] for r in rows]}
                self.commit(phase="create", create=pending)
                result = self.io.request("POST", "/crypto/certificatekeypairs/generate/",
                    {"common_name": pending["name"], "validity_days": 90, "alg": "rsa"})
                self.commit(create=dict(pending, pk=result["pk"]))
            if self.s["phase"] == "create":
                self.reconcile_create()
            if self.s["phase"] == "publish":
                self.io.publish(self.keys())
                self.io.edges(self.keys())
                self.commit(phase="wait")
            if self.s["phase"] == "wait":
                # Every restart waits a fresh monotonic 600 s. No wall-clock
                # elapsed-time inference can shorten publication-before-switch.
                self.io.edges(self.keys())
                self.sleep(WAIT)
                self.io.clock()
                self.io.edges(self.keys())
                self.commit(phase="switch")
            if self.s["phase"] == "switch":
                # Crash during a switch restores old issuance before any retry.
                self.commit(phase="switching")
                self.switch(self.s["next"])
                self.commit(phase="retire", retiring=self.s["current"],
                            current=self.s["next"], next=None,
                            stopped=self.now() + self.io.c["max_inflight"])
            if self.s["phase"] in ("switching", "rollback"):
                self.rollback()
            if self.s["phase"] in ("retire", "rollback_wait"):
                try:
                    self.io.probe(self.s["current"]["jwk"], self.keys())
                except Exception:
                    self.commit(stopped=self.now() + self.io.c["max_inflight"])
                    raise
                # Never infer safe elapsed time solely from wall-clock journal
                # timestamps after a restart or clock step.
                self.sleep(WAIT + self.io.c["max_inflight"])
                self.io.clock()
                self.io.probe(self.s["current"]["jwk"], self.keys())
                require(self.now() >= self.s["stopped"] + WAIT)
                # Ensure later stale enrollment/replica issuance cannot go unseen.
                self.preflight()
                self.switch(self.s["current"])
                old = self.s.get("retiring") or self.s["next"]
                self.commit(phase="prune", prune=old)
            if self.s["phase"] == "prune":
                old = self.s["prune"]
                self.io.patch(self.io.c["stage"]["provider"], self.s["current"]["pk"])
                actual = self.io.request("GET", "/providers/oauth2/" + str(self.io.c["stage"]["provider"]) + "/")
                require(projection(actual) == self.io.c["stage"]["body"]
                        and actual["signing_key"] == self.s["current"]["pk"])
                # Any remaining references prevent deletion; the original key
                # always remains, regardless of household token lifetimes.
                self.io.publish([self.s["current"]["jwk"]])
                self.io.edges([self.s["current"]["jwk"]])
                self.commit(phase="delete", delete=old)
            if self.s["phase"] == "delete":
                old = self.s["delete"]
                if old["owned"] and old["pk"] != ORIGINAL:
                    try:
                        refs = self.io.request("GET", "/crypto/certificatekeypairs/" + old["pk"] + "/used_by/")
                        if not refs:
                            self.io.request("DELETE", "/crypto/certificatekeypairs/" + old["pk"] + "/")
                    except urllib.error.HTTPError as exc:
                        require(exc.code == 404)
                self.commit(phase="idle", retiring=None, next=None, prune=None, delete=None,
                            history=sorted(set(self.s.get("history", []) + [old["pk"]])),
                            rotated=self.now())
        except Exception:
            # Persist alert before trying delivery. Do not log exception bodies.
            self.commit(alert=True)
            if self.s["phase"] in ("switching", "rollback", "retire"):
                try:
                    self.rollback()
                except Exception:
                    pass
            try:
                if self.io.alert():
                    self.commit(alert=False)
            except Exception:
                pass
            raise RuntimeError("signer rotation failed; inspect protected state") from None



def bootstrap(io, save, state):
    """Write-ahead setup. Uncertain creates refuse rather than adopt by name."""
    io.authority()
    require(set(p["slug"] for p in io.c["providers"]) <= set(SLUGS))
    for p in io.c["providers"]:
        row = io.request("GET", "/providers/oauth2/" + str(p["pk"]) + "/")
        require(projection(row) == p["body"] and row["signing_key"] == ORIGINAL)
    if not state:
        state.update(phase="bootstrap", contracts=io.c["providers"], stage={},
                     current={"pk": ORIGINAL, "owned": False, "jwk": io.key(ORIGINAL)}, history=[ORIGINAL],
                     next=None, retiring=None, alert=False, rotated=time.time())
        save(state)
    require(state["phase"] == "bootstrap" and state["contracts"] == io.c["providers"])
    stage = state["stage"]
    require(not state.get("pending") or state["pending"]["kind"] not in stage)
    slug = "homelab-cohort-signer-staging"
    body = copy.deepcopy(io.c["providers"][0]["body"])
    body.update(name=slug, client_id=slug, grant_types=["authorization_code"], encryption_key=None,
                access_token_validity="seconds=300", refresh_token_validity="seconds=0",
                redirect_uris=[], property_mappings=[],
                jwt_federation_sources=[], jwt_federation_providers=[])
    objects = [
        ("provider", "/providers/oauth2/", dict(body, signing_key=ORIGINAL)),
        ("policy", "/policies/expression/", {"name": slug, "expression": "return False",
                                           "execution_logging": False}),
        ("application", "/core/applications/", {"name": slug, "slug": slug,
            "provider": stage.get("provider"), "policy_engine_mode": "all", "backchannel_providers": []}),
        ("binding", "/policies/bindings/", {"target": stage.get("application"),
            "policy": stage.get("policy"), "order": 0, "enabled": True, "negate": False,
            "group": None, "user": None, "timeout": 30}),
    ]
    # Recompute dependent bodies after each persisted returned ID.
    for name, path, request in objects:
        if name == "application":
            request["provider"] = stage["provider"]
        if name == "binding":
            request.update(target=stage["application"], policy=stage["policy"])
        if name in stage:
            lookup = slug if name == "application" else str(stage[name])
            actual = io.request("GET", path + lookup + "/")
            require(actual["pk"] == stage[name] and all(actual[k] == v for k, v in request.items()))
            continue
        pending = state.get("pending")
        if pending:
            require(name in ("provider", "application") and pending["kind"] == name
                    and pending["path"] == path and set(pending) <= {"kind", "path", "body"})
            providers = [p for p in io.rows("/providers/oauth2/?name=" + slug) if p["name"] == slug]
            clients = [p for p in io.rows("/providers/oauth2/?client_id=" + slug) if p["client_id"] == slug]
            ids = {p["pk"] for p in providers + clients}
            apps = io.rows("/core/applications/")
            linked = [a for a in apps if a["slug"] == slug or a["provider"] in ids
                      or ids.intersection(a["backchannel_providers"])]
            try:
                app = io.request("GET", "/core/applications/" + slug + "/")
            except urllib.error.HTTPError as exc:
                require(exc.code == 404)
                app = None
            # Public-body equality cannot prove creator ownership or permissions.
            # Preserve every uncertain intent for exact-PK operator reconciliation.
            require(not providers and not clients and not linked and app is None)
            state["pending"] = None
            save(state)
        if name == "provider":
            require(not any(p["name"] == slug for p in io.rows(path)))
        if name == "application":
            require(not any(p["slug"] == slug for p in io.rows(path)))
        state["pending"] = {"kind": name, "path": path, "body": copy.deepcopy(request)}
        save(state)
        result = io.request("POST", path, request)
        stage[name] = result["pk"]
        state["pending"] = None
        save(state)
        lookup = slug if name == "application" else str(stage[name])
        actual = io.request("GET", path + lookup + "/")
        require(actual["pk"] == stage[name] and all(actual[k] == v for k, v in request.items()))
    stage.update(slug=slug, body=body, policy_name=slug)
    io.c["stage"] = stage
    state["phase"] = "idle"
    save(state)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=("snapshot", "bootstrap", "enrollment", "approve-contracts", "rotate", "publish", "rollback"))
    parser.add_argument("enrollment_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    require(os.geteuid() == 0)
    require(STATE.is_dir() and not STATE.is_symlink())
    st = STATE.stat()
    require(st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o700)
    lock = os.open(STATE / "cohort.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    lock_stat = os.fstat(lock)
    require(stat.S_ISREG(lock_stat.st_mode) and lock_stat.st_uid == 0
            and lock_stat.st_nlink == 1 and stat.S_IMODE(lock_stat.st_mode) == 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    config = private(Path("/etc/authentik-signer.json"))
    require(set(config) <= {"sops_file", "sops_age_key_file", "providers", "edges", "replicas",
                            "cache_ceiling", "max_inflight"})
    require(all(set(p) <= {"pk", "slug", "body", "canary_audience"} for p in config["providers"]))
    try:
        state = private(STATE / "generation.json")
    except FileNotFoundError:
        require(args.command in ("bootstrap", "snapshot"))
        state = {}
    if state and args.command != "snapshot":
        public = {k: state[k] for k in ("phase", "contracts", "current", "history")}
        atomic(Path("/etc/authentik-signer-generation.json"), public, 0o644)
    if args.command == "enrollment":
        require(state["phase"] == "idle")
        require(args.enrollment_args and args.enrollment_args[0] == "ansible-playbook")
        env = dict(os.environ, AUTHENTIK_SIGNER_ENROLLMENT="locked")
        return subprocess.run(["runuser", "-u", "igor", "--", "env",
            "AUTHENTIK_SIGNER_ENROLLMENT=locked", *args.enrollment_args], env=env, check=False).returncode
    # Existing root-protected SOPS access; capture only, never print plaintext.
    sops_env = dict(os.environ)
    if config.get("sops_age_key_file"):
        sops_env["SOPS_AGE_KEY_FILE"] = config["sops_age_key_file"]
    try:
        secret = subprocess.run(["sops", "--decrypt", "--output-type", "json", config["sops_file"]],
                                env=sops_env, check=True, capture_output=True, timeout=30)
    except Exception:
        if state:
            state["alert"] = True
            atomic(STATE / "generation.json", state)
            try:
                if IO(config, {}).alert():
                    state["alert"] = False
                    atomic(STATE / "generation.json", state)
            except Exception:
                pass
        print("Signer protected credential access failed; signing unchanged", file=sys.stderr)
        return 1
    io = IO(config, json.loads(secret.stdout))
    if args.command == "snapshot":
        for p in config["providers"]:
            row = io.request("GET", "/providers/oauth2/" + str(p["pk"]) + "/")
            app = io.request("GET", "/core/applications/" + p["slug"] + "/")
            require(app["provider"] == p["pk"])
            p["body"] = projection(row)
        print(json.dumps(config, indent=2, sort_keys=True))
        return 0
    def save(s):
        atomic(STATE / "generation.json", s)
        # No credentials, certificates or private keys in either record.
        public = {k: s[k] for k in ("phase", "contracts", "current", "history")}
        atomic(Path("/etc/authentik-signer-generation.json"), public, 0o644)
    if args.command == "bootstrap":
        bootstrap(io, save, state)
        return 0
    io.c["stage"] = state["stage"]
    controller = Controller(io, state, save)
    if args.command == "approve-contracts":
        require(state["phase"] == "idle")
        old = {p["slug"]: p for p in state["contracts"]}
        new = {p["slug"]: p for p in config["providers"]}
        require(set(old) <= set(new) <= set(SLUGS))
        for slug, previous in old.items():
            require(new[slug]["pk"] == previous["pk"])
        # Explicit operator rebaseline, never automatic discovery or adoption.
        for p in config["providers"]:
            row = io.request("GET", "/providers/oauth2/" + str(p["pk"]) + "/")
            require(projection(row) == p["body"] and row["signing_key"] == state["current"]["pk"])
        previous = state["contracts"]
        state["contracts"] = config["providers"]
        try:
            controller.preflight()
            io.probe(state["current"]["jwk"], controller.keys())
        except Exception:
            state["contracts"] = previous
            raise RuntimeError("contract approval refused") from None
        save(state)
        return 0
    try:
        controller.run(args.command)
    except Exception:
        print("Authentik signer controller failed; protected journal retained", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
