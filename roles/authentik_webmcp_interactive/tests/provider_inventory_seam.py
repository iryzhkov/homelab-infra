"""Pinned inventory response contract, without an Authentik runtime.

Hash-checked upstream declarations select public/base versus OAuth2 fields.
The actual secret-field representation method runs with a strict synthetic
change-permitted actor. Model/queryset loading is replaced by fixture dictionaries.
Application nesting and view/route bindings are checked against upstream ASTs.
"""
import ast
import copy
from types import SimpleNamespace as NS

# Keep this plain-data serializer seam independent of the Django relation seam
# so the machine-identity fixture can reuse the exact response contract.
import hashlib
import json
from pathlib import Path

FIXTURES = Path(__file__).with_name("upstream")
_provenance = json.loads((FIXTURES / "provenance.json").read_text())
assert _provenance["revision"] == "410f46e5cf18f97a1f0056b30984a145242df55d"
for _filename, _evidence in _provenance["files"].items():
    assert hashlib.sha256((FIXTURES / _filename).read_bytes()).hexdigest() == _evidence["sha256"]


def declaration(filename, name):
    tree = ast.parse((FIXTURES / filename).read_text())
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def value(node, name):
    return next(n.value for n in node.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))


def meta(node):
    return next(n for n in node.body if isinstance(n, ast.ClassDef) and n.name == "Meta")


provider = declaration("provider_api.py.txt", "ProviderSerializer")
PUBLIC_FIELDS = ast.literal_eval(value(meta(provider), "fields"))
assert "client_secret" not in PUBLIC_FIELDS and "client_id" not in PUBLIC_FIELDS
assert {"pk", "name", "property_mappings", "meta_model_name"} <= set(PUBLIC_FIELDS)
oauth = declaration("oauth2_provider_api.py.txt", "OAuth2ProviderSerializer")
assert [b.id for b in oauth.bases] == ["ProviderSerializer"]
OAUTH_FIELDS = eval(compile(ast.Expression(value(meta(oauth), "fields")), "<fields>", "eval"),
                    {"ProviderSerializer": NS(Meta=NS(fields=PUBLIC_FIELDS))})
SECRET_FIELDS = ast.literal_eval(value(meta(oauth), "secret_fields"))
assert "client_secret" in OAUTH_FIELDS and SECRET_FIELDS == ["client_secret"]

application = declaration("application_api.py.txt", "ApplicationSerializer")
APPLICATION_FIELDS = ast.literal_eval(value(meta(application), "fields"))
for field, source in (("provider_obj", "get_provider"),
                      ("backchannel_providers_obj", "backchannel_providers")):
    call = value(application, field)
    assert call.func.id == "ProviderSerializer"
    keywords = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
    assert keywords["source"] == source and keywords["read_only"] is True
    if field == "backchannel_providers_obj":
        assert keywords["many"] is True
for filename, view, serializer in (
        ("provider_api.py.txt", "ProviderViewSet", "ProviderSerializer"),
        ("application_api.py.txt", "ApplicationViewSet", "ApplicationSerializer"),
        ("oauth2_provider_api.py.txt", "OAuth2ProviderViewSet", "OAuth2ProviderSerializer")):
    assert value(declaration(filename, view), "serializer_class").id == serializer
routes = ast.parse((FIXTURES / "core_urls.py.txt").read_text())
for route, view in (("providers/all", "ProviderViewSet"), ("core/applications", "ApplicationViewSet")):
    assert any(isinstance(n, ast.Tuple) and len(n.elts) == 2
               and isinstance(n.elts[0], ast.Constant) and n.elts[0].value == route
               and isinstance(n.elts[1], ast.Name) and n.elts[1].id == view
               for n in ast.walk(routes))


class SelectedFields:
    def to_representation(self, instance):
        return {k: copy.deepcopy(v) for k, v in vars(instance).items() if k in self.Meta.fields}


# Run the pinned permission-dependent secret-field removal method unchanged.
node = declaration("api_utils.py.txt", "ModelSerializer")
node.body = [n for n in node.body if isinstance(n, ast.FunctionDef)
             and n.name == "to_representation"]
namespace = {"BaseModelSerializer": SelectedFields, "Model": object}
exec(compile(ast.Module(body=[node], type_ignores=[]), "<pinned-secret-fields>", "exec"), namespace)


def represent(row, fields, secret_fields=()):
    serializer = namespace["ModelSerializer"]()
    serializer.Meta = NS(fields=fields, secret_fields=secret_fields)
    serializer.context = {"request": NS(user=NS(has_perm=lambda permission, instance: True))}
    return serializer.to_representation(NS(
        **row, _meta=NS(app_label="authentik_providers_oauth2", model_name="oauth2provider")))


def public_provider(row):
    return represent(row, PUBLIC_FIELDS)


def oauth_provider(row):
    return represent(row, OAUTH_FIELDS, SECRET_FIELDS)


def application_row(row, providers):
    result = represent(row, APPLICATION_FIELDS)
    by_pk = {r["pk"]: r for r in providers}
    linked = by_pk.get(row.get("provider"))
    result["provider_obj"] = public_provider(linked) if linked else None
    result["backchannel_providers_obj"] = [
        public_provider(by_pk[pk]) for pk in row.get("backchannel_providers", [])]
    return result
