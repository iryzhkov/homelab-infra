#!/usr/bin/env python3
"""Offline signer fault injection. HOME must be explicit scratch."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ["HOME"])
assert str(HOME).startswith("/work/.cache/") or os.environ.get("SIGNER_TEST_HOME") == str(HOME)
assert str(HOME) != "/root"
spec = importlib.util.spec_from_file_location("signer", ROOT / "roles/authentik_signer/files/authentik-signer.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def b64(n):
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length()+7)//8, "big")).decode().rstrip("=")

def key(kid):
    return dict(kid=kid, kty="RSA", use="sig", alg="RS256", n=b64(2**4095+1), e="AQAB")

OLD = dict(pk=m.ORIGINAL, jwk=key("old"), owned=False)
NEW = dict(pk="new-uuid", jwk=key("new"), owned=True)


class Crash(BaseException):
    pass


class Fixture:
    def __init__(self):
        body = {f: [] if f in ("grant_types", "redirect_uris", "property_mappings",
                               "jwt_federation_sources", "jwt_federation_providers") else "" for f in m.FIELDS}
        body.update(encryption_key=None, access_token_validity="seconds=300", issuer_mode="per_provider")
        self.secrets = {"signer_canaries": {}}
        self.c = dict(providers=[dict(pk=i+1, slug=s, body=copy.deepcopy(body), canary_audience=s)
                                 for i,s in enumerate(m.SLUGS[:-1])],
                      cache_ceiling=300, max_inflight=60, edges=["1.1.1.1", "2.2.2.2"], replicas=["https://replica"])
        body.update(name="stage", grant_types=["authorization_code"])
        self.c["stage"] = dict(provider=900, application="app", slug="stage", policy="deny",
                              policy_name="deny", binding="binding", body=body)
        self.providers = {p["pk"]: dict(copy.deepcopy(p["body"]), pk=p["pk"], signing_key=m.ORIGINAL)
                          for p in self.c["providers"]}
        self.providers[900] = dict(copy.deepcopy(body), pk=900, signing_key=m.ORIGINAL)
        self.s = dict(phase="idle", contracts=self.c["providers"], stage=self.c["stage"],
                      current=copy.deepcopy(OLD), next=None, retiring=None, alert=False,
                      rotated=0, history=[m.ORIGINAL])
        self.persisted = copy.deepcopy(self.s)
        self.t = m.MONTH+1
        self.keys = [OLD["jwk"]]
        self.generated = []
        self.events, self.saved = [], 0
        self.crash_at = self.fail_patch = None
        self.publication_fail = self.feed_fail = False
        self.foreign_refs = []

    def save(self, state):
        self.saved += 1
        self.persisted = copy.deepcopy(state)
        if self.saved == self.crash_at:
            raise Crash()

    def request(self, method, path, body=None):
        self.events.append((method, path, copy.deepcopy(body)))
        if path == "/crypto/certificatekeypairs/generate/":
            self.generated.append(dict(pk=NEW["pk"], name=body["common_name"]))
            return self.generated[-1]
        if path.endswith("/used_by/"):
            return self.foreign_refs
        if method == "DELETE":
            return None
        if path.startswith("/providers/oauth2/"):
            pk = int(path.split("/")[3])
            if method == "PATCH":
                assert set(body) == {"signing_key"}
                self.providers[pk]["signing_key"] = body["signing_key"]
                if self.fail_patch == pk:
                    self.fail_patch = None
                    raise OSError("lost PATCH response")
            return copy.deepcopy(self.providers[pk])
        if path.startswith("/core/applications/"):
            slug = path.split("/")[3]
            return dict(provider=900, slug=slug, pk="app", backchannel_providers=[], policy_engine_mode="all") if slug == "stage" else dict(
                provider=next(p["pk"] for p in self.c["providers"] if p["slug"] == slug), slug=slug)
        if path.startswith("/policies/expression/"):
            return dict(expression="return False", name="deny")
        raise AssertionError((method,path))

    def rows(self, path):
        if path == "/providers/oauth2/":
            return list(self.providers.values())
        if path == "/crypto/certificatekeypairs/":
            return copy.deepcopy(self.generated)
        if path == "/core/applications/":
            return [dict(slug="stage", provider=900, backchannel_providers=[])]
        if path == "/policies/bindings/":
            return [dict(target="app", pk="binding", policy="deny", enabled=True, negate=False, group=None, user=None)]
        raise AssertionError(path)

    def patch(self, pk, key):
        self.request("PATCH", "/providers/oauth2/"+str(pk)+"/", dict(signing_key=key))

    def key(self, pk, staging=False):
        if staging:
            self.patch(900, pk)
        return copy.deepcopy(NEW["jwk"])

    check_canaries = m.IO.check_canaries

    def authority(self):
        pass

    def clock(self):
        pass

    def publish(self, keys):
        if self.publication_fail:
            raise OSError()
        self.keys = copy.deepcopy(keys)

    def edges(self, keys):
        if m.jwks(keys) != m.jwks(self.keys):
            raise OSError("edge mismatch")

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))
        self.t += seconds

    def probe(self, expected, keys):
        for p in self.c["providers"]:
            require_key = m.ORIGINAL if expected["kid"] == "old" else NEW["pk"]
            assert self.providers[p["pk"]]["signing_key"] == require_key

    def alert(self):
        return not self.feed_fail

    def run(self, command="rotate"):
        return m.Controller(self, self.s, self.save, lambda:self.t, self.sleep).run(command)

    def restart(self):
        self.s = copy.deepcopy(self.persisted)
        self.crash_at = None


class BootstrapFixture:
    def __init__(self):
        f=Fixture()
        self.c=f.c
        for field in ("jwt_federation_sources","jwt_federation_providers"):
            self.c["providers"][0]["body"][field]=["foreign"]
            f.providers[1][field]=["foreign"]
        self.initial=f
        self.objects={}
        self.posts=[]
        self.gets=[]
    def authority(self): pass
    def key(self,pk): return OLD["jwk"]
    def rows(self,path):
        self.gets.append(path)
        return [v for (p,k),v in self.objects.items() if p==path.split("?")[0]]
    def request(self,method,path,body=None):
        if method=="POST":
            self.posts.append(path)
            pk=900 if path=="/providers/oauth2/" else "uuid-"+str(len(self.posts))
            value=dict(body,pk=pk)
            lookup=body["slug"] if path=="/core/applications/" else str(pk)
            self.objects[(path,lookup)]=value
            return value
        for (prefix,lookup),value in self.objects.items():
            if path==prefix+lookup+"/": return value
        self.gets.append(path)
        if path.startswith("/core/applications/"):
            raise m.urllib.error.HTTPError(path,404,"absent",{},None)
        return self.initial.request(method,path,body)


class ControllerTests(unittest.TestCase):
    def test_rotate_and_original_preserved(self):
        f=Fixture(); f.run()
        self.assertEqual(f.s["phase"], "idle")
        self.assertEqual(f.keys, [NEW["jwk"]])
        self.assertEqual(len(f.generated), 1)
        self.assertFalse(any(e[0]=="DELETE" for e in f.events))
        self.assertIn(("sleep", 600), f.events)
        self.assertIn(("sleep", 660), f.events)

    def test_every_journal_crash_boundary(self):
        reference=Fixture(); reference.run()
        for boundary in range(1, reference.saved+1):
            with self.subTest(boundary=boundary):
                f=Fixture(); f.crash_at=boundary
                with self.assertRaises(Crash): f.run()
                f.restart()
                try: f.run()
                except RuntimeError:
                    # Only a crash before the first POST may leave an uncertain
                    # zero-match intent. It must refuse without a second create.
                    self.assertEqual(f.s["phase"], "create")
                    self.assertEqual(f.generated, [])
                    continue
                self.assertLessEqual(len(f.generated), 1)
                self.assertEqual(f.s["phase"], "idle")
                kids={k["kid"] for k in f.keys}
                expected="old" if f.s["current"]["pk"]==m.ORIGINAL else "new"
                self.assertIn(expected,kids)

    def test_every_failure_and_rollback_journal_boundary(self):
        reference=Fixture(); reference.fail_patch=3; reference.feed_fail=True
        with self.assertRaises(RuntimeError): reference.run()
        boundaries=reference.saved
        for boundary in range(1,boundaries+1):
            with self.subTest(boundary=boundary):
                f=Fixture(); f.fail_patch=3; f.feed_fail=True; f.crash_at=boundary
                try: f.run()
                except (Crash,RuntimeError): pass
                f.restart(); f.feed_fail=False
                for recovery in range(3):
                    try: f.run()
                    except RuntimeError:
                        if f.s["phase"]=="create" and not f.generated: break
                        continue
                    if f.s["phase"]=="idle": break
                self.assertLessEqual(len(f.generated),1)
                for p in f.c["providers"]:
                    pk=f.providers[p["pk"]]["signing_key"]
                    kid="old" if pk==m.ORIGINAL else "new"
                    self.assertIn(kid,{k["kid"] for k in f.keys})
                if f.s["phase"]!="create":
                    self.assertEqual(f.s["phase"],"idle")

    def test_manual_rollback_after_key_removal_refuses_without_mutation(self):
        for phase in ("prune", "delete", "idle"):
            with self.subTest(phase=phase):
                f=Fixture()
                publish,save=f.publish,f.save
                def publish_then_crash(keys):
                    publish(keys)
                    if phase=="prune" and keys==[NEW["jwk"]]: raise Crash()
                def save_then_crash(state):
                    save(state)
                    if phase=="delete" and state["phase"]=="delete": raise Crash()
                f.publish,f.save=publish_then_crash,save_then_crash
                try: f.run()
                except Crash: f.restart()
                self.assertEqual(f.s["phase"],phase)
                self.assertEqual(f.keys,[NEW["jwk"]])
                f.publish,f.save=publish,save
                providers,keys=copy.deepcopy(f.providers),copy.deepcopy(f.keys)
                with self.assertRaises(RuntimeError): f.run("rollback")
                self.assertEqual(f.providers,providers)
                self.assertEqual(f.keys,keys)

    def test_refresh_canaries_and_multiple_replicas_refused_in_preflight(self):
        for change in ("refresh", "replicas"):
            f=Fixture()
            if change=="refresh":
                f.c["providers"][0]["body"]["grant_types"]=["client_credentials"]
                f.providers[1]["grant_types"]=["client_credentials"]
                f.secrets["signer_canaries"][m.SLUGS[0]]={"grant_type":"refresh_token"}
            else:
                f.c["replicas"].append("https://second")
            with self.assertRaises(RuntimeError): f.run()
            self.assertEqual(f.generated,[])
            self.assertFalse(any(e[0]=="PATCH" for e in f.events))

    def test_non_client_credentials_providers_need_no_token_probe(self):
        f=Fixture()
        io=m.IO(f.c,{"signer_canaries":{}})
        with patch.object(io.opener,"open",side_effect=AssertionError("token exchange")):
            io.check_canaries()
            io.probe(OLD["jwk"],[OLD["jwk"]])

    def test_failure_before_patch_preserves_switch_for_retry(self):
        f=Fixture()
        f.s.update(phase="switch",next=copy.deepcopy(NEW))
        f.keys=[OLD["jwk"],NEW["jwk"]]
        f.providers[900]["signing_key"]=NEW["pk"]
        edges=f.edges
        f.edges=lambda keys: (_ for _ in ()).throw(OSError())
        with self.assertRaises(RuntimeError): f.run()
        self.assertEqual(f.s["phase"],"switch")
        self.assertFalse(any(e[0]=="PATCH" for e in f.events))
        f.edges=edges
        f.run()
        self.assertEqual(f.s["current"],NEW)

    def test_publication_failure_keeps_old(self):
        f=Fixture(); f.publication_fail=True
        with self.assertRaises(RuntimeError): f.run()
        self.assertTrue(all(f.providers[p["pk"]]["signing_key"]==m.ORIGINAL for p in f.c["providers"]))

    def test_serializer_drift_restores_all_signers_without_repairing_fields(self):
        f=Fixture()
        original=f.patch
        def patch_provider(pk,key):
            original(pk,key)
            if pk==3 and key==NEW["pk"]:
                f.providers[3]["grant_types"]=["unexpected"]
        f.patch=patch_provider
        with self.assertRaises(RuntimeError): f.run()
        self.assertEqual(f.s["phase"],"rollback")
        self.assertTrue(all(f.providers[p["pk"]]["signing_key"]==m.ORIGINAL for p in f.c["providers"]))
        self.assertEqual(f.providers[3]["grant_types"],["unexpected"])
        self.assertEqual({k["kid"] for k in f.keys},{"old","new"})

    def test_partial_switch_rollback_and_feed_retry(self):
        f=Fixture(); f.fail_patch=3; f.feed_fail=True
        with self.assertRaises(RuntimeError): f.run()
        self.assertEqual(f.s["phase"], "rollback_wait")
        self.assertTrue(f.s["alert"])
        self.assertEqual({k["kid"] for k in f.keys}, {"old","new"})
        self.assertTrue(all(f.providers[p["pk"]]["signing_key"]==m.ORIGINAL for p in f.c["providers"]))
        f.feed_fail=False; f.run()
        self.assertFalse(f.s["alert"])
        self.assertEqual(f.keys, [OLD["jwk"]])

    def test_wait_restarts_after_crash(self):
        f=Fixture()
        def crash_sleep(n): raise Crash()
        f.sleep=crash_sleep
        with self.assertRaises(Crash): f.run()
        self.assertEqual(f.s["phase"],"wait")
        f.restart(); f.sleep=lambda n: setattr(f,"t",f.t+n)
        f.run()
        self.assertEqual(f.s["current"]["pk"],NEW["pk"])

    def test_stale_issuance_restarts_retirement(self):
        f=Fixture()
        def stop(n):
            if n==660: raise Crash()
            f.t+=n
        f.sleep=stop
        with self.assertRaises(Crash): f.run()
        f.restart(); f.providers[1]["signing_key"]=m.ORIGINAL
        with self.assertRaises(RuntimeError): f.run()
        self.assertEqual(f.s["phase"],"rollback_wait")
        self.assertEqual({k["kid"] for k in f.keys},{"old","new"})

    def test_staging_bound_or_grant_enabled_refuses(self):
        for field,value in (("grant_types",[]),("grant_types",["client_credentials"]),
                            ("redirect_uris",[{"url":"https://foreign"}]),("encryption_key","foreign")):
            f=Fixture(); f.providers[900][field]=value
            with self.assertRaises(RuntimeError): f.run()
            self.assertEqual(f.generated,[])

    def test_oversize_lifetime_and_cache_refuse(self):
        for change in ("lifetime","cache"):
            f=Fixture()
            if change=="lifetime": f.providers[1]["access_token_validity"]="seconds=301"
            else: f.c["cache_ceiling"]=301
            with self.assertRaises(RuntimeError): f.run()
            self.assertEqual(f.generated,[])

    def test_invalid_private_duplicate_keys_refused(self):
        for bad in (dict(key("x"), d="private"), dict(key("x"), alg="RS512"),
                    dict(key("x"), n="AA"), dict(key("x"), e="Aw"),
                    dict(key("x"), use="enc"),dict(key("x"), n="invalid=")):
            with self.subTest(bad=list(bad)):
                with self.assertRaises((RuntimeError,ValueError)): m.jwks([bad])
        with self.assertRaises(RuntimeError): m.jwks([key("x"),key("x")])


    def test_rsa4096_pyjwt(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        private_key=rsa.generate_private_key(public_exponent=65537,key_size=4096)
        numbers=private_key.public_key().public_numbers()
        public=dict(kid="rsa4096",kty="RSA",use="sig",alg="RS256",n=b64(numbers.n),e=b64(numbers.e))
        m.jwks([public])
        token=jwt.encode({"iss":"fixture","aud":"resource","exp":4102444800},
                         private_key,algorithm="RS256",headers={"kid":"rsa4096"})
        decoded=jwt.decode(token,jwt.PyJWK(public).key,algorithms=["RS256"],audience="resource",issuer="fixture")
        self.assertEqual(decoded["aud"],"resource")

    def test_stale_enrollment_converges_only_approved_provider(self):
        spec=importlib.util.spec_from_file_location("cohort",ROOT/"module_utils/cohort_signer.py")
        cohort=importlib.util.module_from_spec(spec); spec.loader.exec_module(cohort)
        f=Fixture()
        with tempfile.TemporaryDirectory(dir=HOME) as directory:
            state=Path(directory)/"generation.json"
            state.write_text(json.dumps(dict(f.s,current=NEW)))
            state.chmod(0o644)
            prior={"objects":{"provider":{"pk":1,"body":{"signing_key":m.ORIGINAL,"name":"original"}}}}
            with patch.object(cohort,"PATH",str(state)), patch.dict(os.environ,AUTHENTIK_SIGNER_ENROLLMENT="locked"):
                approved=cohort.receipt(prior)
                self.assertEqual(approved["objects"]["provider"]["body"]["signing_key"],NEW["pk"])
                self.assertEqual(approved["objects"]["provider"]["body"]["name"],"original")
                with self.assertRaises(RuntimeError): cohort.signer(m.SLUGS[0],"foreign")
                self.assertEqual(cohort.body(777,{"signing_key":m.ORIGINAL}),{"signing_key":m.ORIGINAL})
            with patch.object(cohort,"PATH",str(state)), patch.dict(os.environ,AUTHENTIK_SIGNER_ENROLLMENT=""):
                with self.assertRaises(RuntimeError): cohort.generation()

    def test_atomic_crash_around_replace_and_fsync(self):
        with tempfile.TemporaryDirectory(dir=HOME) as directory:
            p=Path(directory)/"state"
            m.atomic(p,{"phase":"old"})
            with patch.object(m.os,"replace",side_effect=OSError):
                with self.assertRaises(OSError): m.atomic(p,{"phase":"new"})
            self.assertEqual(json.loads(p.read_bytes()),{"phase":"old"})
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            real_fsync=m.os.fsync
            calls=[]
            def fsync(fd):
                calls.append(fd)
                if len(calls)==2: raise OSError()
                real_fsync(fd)
            with patch.object(m.os,"fsync",side_effect=fsync):
                with self.assertRaises(OSError): m.atomic(p,{"phase":"new"})
            self.assertEqual(json.loads(p.read_bytes()),{"phase":"new"})

    def test_old_owned_key_with_foreign_references_is_never_deleted(self):
        f=Fixture()
        # Preserve another provider's reference even for a rotation-owned key.
        f.s["current"]["owned"]=True
        f.foreign_refs=[dict(pk="foreign")]
        f.run()
        self.assertFalse(any(e[0]=="DELETE" for e in f.events))


    def test_bootstrap_crash_boundaries_never_duplicate_provider(self):
        for boundary in range(1,11):
            io=BootstrapFixture(); state={}; persisted={}; count=0
            def save(s):
                nonlocal persisted,count
                persisted=copy.deepcopy(s); count+=1
                if count==boundary: raise Crash()
            try: m.bootstrap(io,save,state)
            except Crash: pass
            state=copy.deepcopy(persisted)
            try:
                if state.get("phase")=="bootstrap":
                    m.bootstrap(io,lambda s:None,state)
            except RuntimeError:
                self.assertIsNotNone(state.get("pending"))
            self.assertLessEqual(io.posts.count("/providers/oauth2/"),1)
            for (path,lookup),value in io.objects.items():
                if path=="/providers/oauth2/":
                    self.assertEqual(value["grant_types"],["authorization_code"])
                    self.assertEqual(value["property_mappings"],[])
                    self.assertEqual(value["jwt_federation_sources"],[])
                    self.assertEqual(value["jwt_federation_providers"],[])
            if state.get("phase")=="idle":
                stage=state["stage"]
                self.assertEqual(stage["provider"],900)
                self.assertTrue(stage["binding"])

    def test_bootstrap_pending_create_reconciliation(self):
        slug="homelab-cohort-signer-staging"
        for outcome in ("absent", "adopt", "adopt_application", "mismatch", "duplicate", "foreign_app", "legacy_found", "recorded_provider"):
            with self.subTest(outcome=outcome):
                io=BootstrapFixture(); state={}
                def crash(s):
                    if s.get("pending",{}).get("kind")=="provider": raise Crash()
                with self.assertRaises(Crash): m.bootstrap(io,crash,state)
                pending=copy.deepcopy(state["pending"])
                if outcome=="absent":
                    # Exact installed receipt @7: no body, no returned ID.
                    state["pending"]={"kind":"provider","path":"/providers/oauth2/"}
                else:
                    provider=dict(pending["body"],pk=900)
                    io.objects[("/providers/oauth2/","900")]=provider
                    if outcome=="mismatch": provider["grant_types"]=["client_credentials"]
                    if outcome=="duplicate": io.objects[("/providers/oauth2/","901")]=dict(provider,pk=901)
                    if outcome=="foreign_app":
                        io.objects[("/core/applications/",slug)]=dict(pk="foreign",slug=slug,provider=999)
                    if outcome=="legacy_found": state["pending"].pop("body")
                    if outcome=="recorded_provider": state["stage"]["provider"]=900
                    if outcome=="adopt_application":
                        # Provider is already journaled; app POST loses its reply.
                        def lose_app(method,path,body=None):
                            result=request(method,path,body)
                            if method=="POST" and path=="/core/applications/": raise Crash()
                            return result
                        request=io.request; io.request=lose_app
                        with self.assertRaises(Crash): m.bootstrap(io,lambda s:None,state)
                        io.request=request
                before=copy.deepcopy(state)
                if outcome in ("mismatch","duplicate","foreign_app","legacy_found","recorded_provider"):
                    with self.assertRaises(RuntimeError): m.bootstrap(io,lambda s:None,state)
                    self.assertEqual(state,before)
                    self.assertEqual(io.posts,[])
                else:
                    m.bootstrap(io,lambda s:None,state)
                    self.assertEqual(state["phase"],"idle")
                    self.assertIsNone(state["pending"])
                    self.assertEqual(io.posts.count("/providers/oauth2/"),1 if outcome=="absent" else 0)
                    self.assertEqual(io.posts.count("/core/applications/"),1)
                    self.assertIn("/providers/oauth2/?name="+slug,io.gets)
                    self.assertIn("/core/applications/"+slug+"/",io.gets)

    def test_publication_command_quotes_python_as_one_argument(self):
        import shlex
        io=m.IO({}, {})
        with patch.object(m.subprocess,"run") as run:
            io.publish([OLD["jwk"]])
        argv=run.call_args.args[0]
        remote=shlex.split(" ".join(argv[argv.index("homelab")+1:]))
        self.assertEqual(remote[:4],["sudo","-n","python3","-c"])
        compile(remote[4],"<remote>","exec")
        self.assertEqual(run.call_args.kwargs["input"],m.jwks([OLD["jwk"]]))

    def test_clock_regression_blocks_retirement_and_restores_old(self):
        f=Fixture()
        def sleep(n):
            if n==660: f.t-=120
            else: f.t+=n
        f.sleep=sleep
        with self.assertRaises(RuntimeError): f.run()
        self.assertEqual(f.s["phase"],"rollback_wait")
        self.assertEqual({k["kid"] for k in f.keys},{"old","new"})

    def test_route_renders_exact_scope_with_optional_akasha(self):
        route=(ROOT/"roles/traefik/templates/authentik-jwks.yml.j2").read_text()
        self.assertNotIn("PathPrefix",route)
        self.assertNotIn("staging/jwks",route)
        for slug in m.SLUGS: self.assertIn("/application/o/"+slug+"/jwks/",route)
        self.assertIn("priority: 10000",route)
        self.assertIn("Method(`GET`, `HEAD`)",route)
        nginx=(ROOT/"stacks/observability/files/nginx.conf").read_text()
        self.assertIn("location = /signer-jwks.json",nginx)
        self.assertIn('Cache-Control "no-store"',nginx)
        self.assertIn("limit_except GET { deny all; }",nginx)
        self.assertIn("authentik_signer_routes_enabled: false",(ROOT/"roles/traefik/defaults/main.yml").read_text())
        import jinja2
        import yaml
        env=jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT/"roles/traefik/templates"))
        from ansible.plugins.filter.core import to_bool
        env.filters["bool"] = to_bool
        template=env.get_template("authentik-jwks.yml.j2")
        for enrolled in (False,True):
            rendered=template.render(ansible_managed="fixture",traefik_entrypoint="websecure",
                traefik_cert_resolver="test",traefik_wildcard_only=True,traefik_wildcard_domain="ryzhkov.dev",
                hostvars={"homelab":{"ansible_host":"192.0.2.1"}},observability_ingest_backend_port=8429,
                authentik_signer_akasha_enrolled=enrolled)
            doc=yaml.safe_load(rendered)
            router=doc["http"]["routers"]["cohort-jwks"]
            self.assertEqual(router["priority"],10000)
            self.assertEqual(router["rule"].count("Path("),10 if enrolled else 9)
            self.assertEqual("chaldea-akasha/jwks/" in router["rule"],enrolled)
            self.assertEqual(doc["http"]["middlewares"]["cohort-jwks-file"]["replacePath"]["path"],"/signer-jwks.json")


    def test_native_key_is_public_and_matches_certificate(self):
        import datetime as dt
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        private_key=rsa.generate_private_key(public_exponent=65537,key_size=4096)
        numbers=private_key.public_key().public_numbers()
        public=dict(kid="native-kid",kty="RSA",use="sig",alg="RS256",n=b64(numbers.n),e=b64(numbers.e))
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"fixture")])
        now=dt.datetime.now(dt.timezone.utc)
        def certificate(days):
            return (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(private_key.public_key())
                .serial_number(1).not_valid_before(now-dt.timedelta(days=1)).not_valid_after(now+dt.timedelta(days=days))
                .sign(private_key,hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())
        cert=certificate(90)
        io=m.IO(dict(stage=dict(provider=900,slug="stage",body={f:"" for f in m.FIELDS})), {})
        def request(method,path,body=None):
            if path.endswith("/view_certificate/"): return dict(data=cert)
            if path.startswith("/providers/oauth2/"):
                return dict(io.c["stage"]["body"], pk=900, signing_key="fixture")
            return dict(key_type="rsa",private_key_available=True)
        io.request=request
        io.patch=lambda *a:None
        io.public=lambda u:json.dumps(dict(keys=[dict(public,x5c=["public-certificate"])])).encode()
        self.assertEqual(io.key("fixture",staging=True),public)
        cert=certificate(75)
        self.assertEqual(io.key("fixture",staging=True),public)
        cert=certificate(59)
        with self.assertRaises(RuntimeError): io.key("fixture",staging=True)
        cert=certificate(90)
        io.public=lambda u:json.dumps(dict(keys=[dict(public,d="synthetic-private-field")])).encode()
        with self.assertRaises(RuntimeError): io.key("fixture",staging=True)
        io.public=lambda u:json.dumps(dict(keys=[dict(public,n=b64(numbers.n+2))])).encode()
        with self.assertRaises(RuntimeError): io.key("fixture",staging=True)

    def test_replica_canary_validates_signature_audience_and_expiry_skew(self):
        import time
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        private_key=rsa.generate_private_key(public_exponent=65537,key_size=4096)
        n=private_key.public_key().public_numbers()
        public=dict(kid="canary",kty="RSA",use="sig",alg="RS256",n=b64(n.n),e=b64(n.e))
        io=m.IO(dict(providers=[dict(slug="fixture",canary_audience="resource",body=dict(grant_types=["client_credentials"]))],replicas=["https://replica"]),
                dict(signer_canaries=dict(fixture=dict(grant_type="client_credentials"))))
        now=int(time.time())
        claims=dict(iss=m.ORIGIN+"/application/o/fixture/",aud="resource",iat=now,exp=now+300)
        class Response:
            def __init__(self,c):
                self.data=json.dumps(dict(access_token=jwt.encode(c,private_key,algorithm="RS256",headers={"kid":"canary"}))).encode()
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self,*a): return self.data
        class Opener:
            def open(self,*a,**kw): return Response(claims)
        io.opener=Opener()
        io.probe(public,[public])
        for change in (dict(aud="foreign"),dict(exp=now+301),dict(iat=now-61),dict(exp=now-1)):
            saved=copy.deepcopy(claims); claims.update(change)
            with self.assertRaises(RuntimeError): io.probe(public,[public])
            claims=saved


    def test_actual_nginx_publication_and_method_scope(self):
        import re
        import shutil
        import socket
        import time
        import urllib.error
        import urllib.request
        self.assertIsNotNone(shutil.which("nginx"),"nginx is required for serving checks")
        with tempfile.TemporaryDirectory(dir=HOME) as directory:
            root=Path(directory); public=root/"cohort.json"
            public.write_bytes(m.jwks([OLD["jwk"]]))
            location=re.search(r"    location = /signer-jwks.json \{.*?\n    \}",
                (ROOT/"stacks/observability/files/nginx.conf").read_text(),re.S).group()
            location=location.replace("/srv/jwks/cohort.json",str(public))
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]
            conf=root/"nginx.conf"
            conf.write_text("user root; pid "+str(root/"nginx.pid")+"; error_log stderr; events {} http { access_log off; "
                "client_body_temp_path "+str(root/"body")+"; proxy_temp_path "+str(root/"proxy")+"; "
                "fastcgi_temp_path "+str(root/"fastcgi")+"; uwsgi_temp_path "+str(root/"uwsgi")+"; "
                "scgi_temp_path "+str(root/"scgi")+"; server { listen 127.0.0.1:"+str(port)+"; "
                +location+" location / { return 404; } } }")
            proc=subprocess.Popen(["nginx","-p",str(root),"-c",str(conf),"-g","daemon off;"],
                                  stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            def get(method="GET",path="/signer-jwks.json"):
                return urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:"+str(port)+path,method=method),timeout=1)
            try:
                for attempt in range(100):
                    try:
                        response=get(); break
                    except OSError:
                        if proc.poll() is not None: self.fail("nginx exited: " + proc.stderr.read().decode())
                        time.sleep(.02)
                else: self.fail("nginx did not start")
                with response:
                    self.assertEqual(response.read(),m.jwks([OLD["jwk"]]))
                    self.assertEqual(response.headers["Content-Type"],"application/json")
                    self.assertEqual(response.headers["Cache-Control"],"no-store")
                with get("HEAD") as response:
                    self.assertEqual(response.read(),b"")
                for method,path,status in (("POST","/signer-jwks.json",403),("GET","/signer-jwks.json/",404),("GET","/unknown",404)):
                    with self.assertRaises(urllib.error.HTTPError) as error: get(method,path)
                    self.assertEqual(error.exception.code,status)
                m.atomic(public,dict(keys=[OLD["jwk"],NEW["jwk"]]),0o644)
                with get() as response:
                    self.assertEqual(json.load(response)["keys"],[OLD["jwk"],NEW["jwk"]])
            finally:
                proc.terminate(); proc.wait(timeout=5)
                proc.stderr.close()

if __name__=="__main__":
    unittest.main()
