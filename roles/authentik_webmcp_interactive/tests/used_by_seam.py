"""Pinned UsedByMixin relation seam: real Django managers, DRF and SQLite.

Only unrelated model fields/bases/imports are omitted. Relevant fields, concrete
inheritance, model labels, explicit through models and __str__ bodies are AST
extracted from byte-for-byte upstream snapshots; no used_by rows are mocked.
The guardian stand-in asserts strict superuser and returns the actual queryset.
This is producer qualification, not live RBAC or full Authentik qualification.
"""
import ast
import hashlib
import json
from enum import Enum
from inspect import getmembers
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from uuid import uuid4

home = Path(os.environ["HOME"]).resolve()
assert home == Path(os.environ["WEBMCP_TEST_HOME"]).resolve()
assert Path(os.environ["TMPDIR"]).resolve().is_relative_to(home)
assert not home.is_relative_to("/tmp")

from django.apps import AppConfig
from django.conf import settings

FIXTURES = Path(__file__).with_name("upstream")
provenance = json.loads((FIXTURES / "provenance.json").read_text())
assert provenance["revision"] == "410f46e5cf18f97a1f0056b30984a145242df55d"
for filename, evidence in provenance["files"].items():
    assert hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest() == evidence["sha256"]
configs = []
for name, label in (("seam_core", "authentik_core"),
                    ("seam_oauth2", "authentik_providers_oauth2"),
                    ("seam_policies", "authentik_policies")):
    module = ModuleType(name)
    module.__file__ = __file__
    config = AppConfig(name, module)
    config.label = label
    configs.append(config)
settings.configure(INSTALLED_APPS=configs,
                   DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
                   SECRET_KEY="isolated-used-by-seam", DEFAULT_AUTO_FIELD="django.db.models.AutoField")
import django
django.setup()
from django.db import connection, models
from django.db.models.base import Model
from django.db.models.deletion import SET_DEFAULT, SET_NULL
from django.db.models.manager import Manager
from rest_framework.decorators import action
from rest_framework.fields import CharField, ChoiceField
from rest_framework.response import Response
from rest_framework.serializers import Serializer

namespace = {"models": models, "uuid4": uuid4, "_": lambda value: value,
             "validate_slug": lambda value: None}


def extract_model(filename, name, fields, bases, module, meta=False):
    # Upstream also contains unrelated Python 3.12 generic methods. Hosted
    # fixtures use Python 3.11, so bound the source to this top-level class
    # before parsing. No selected declaration or annotation is rewritten.
    source = (FIXTURES / filename).read_text()
    blocks = source.split("\nclass ")
    block = next(part for part in blocks if part.startswith(name + "("))
    tree = ast.parse("class " + block)
    node = tree.body[0]
    assert isinstance(node, ast.ClassDef) and node.name == name
    # Preserve selected field declarations and exact model-generated strings.
    selected = []
    seen = set()
    for item in node.body:
        if isinstance(item, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in fields for t in item.targets):
            selected.append(item)
            seen.update(t.id for t in item.targets if isinstance(t, ast.Name))
        elif isinstance(item, ast.FunctionDef) and item.name == "__str__":
            selected.append(item)
        elif meta and isinstance(item, ast.ClassDef) and item.name == "Meta":
            selected.append(item)
    assert seen == set(fields), (name, seen, fields)
    node.bases = [ast.parse(base, mode="eval").body for base in bases]
    node.body = selected or [ast.Pass()]
    namespace["__name__"] = module + ".models"
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 str(FIXTURES / filename), "exec"), namespace)
    return namespace[name]


# Non-relation abstract mixins have no effect on these reverse managers.
# SimpleThroughModel itself is extracted, including its abstract Meta.
namespace["InternallyManagedMixin"] = type("InternallyManagedMixin", (), {})
extract_model("lib_models.py.txt", "SimpleThroughModel", [],
              ["models.Model", "InternallyManagedMixin"], "seam_core", meta=True)
extract_model("policy_models.py.txt", "PolicyBindingModel", ["pbm_uuid"],
              ["models.Model"], "seam_policies")
extract_model("core_models.py.txt", "PropertyMapping", ["pm_uuid", "name", "expression"],
              ["models.Model"], "seam_core", meta=True)
extract_model("core_models.py.txt", "Provider", ["name", "property_mappings"],
              ["models.Model"], "seam_core")
extract_model("core_models.py.txt", "Source",
              ["name", "slug", "user_property_mappings", "group_property_mappings"],
              ["PolicyBindingModel"], "seam_core")
for name in ("ProviderPropertyMapping", "SourceUserPropertyMapping", "SourceGroupPropertyMapping"):
    extract_model("core_models.py.txt", name,
                  ["property_mapping", "provider" if name == "ProviderPropertyMapping" else "source"],
                  ["SimpleThroughModel"], "seam_core", meta=True)
extract_model("oauth2_models.py.txt", "ScopeMapping", ["scope_name", "description"],
              ["PropertyMapping"], "seam_oauth2", meta=True)
extract_model("oauth2_models.py.txt", "OAuth2Provider", [], ["Provider"], "seam_oauth2", meta=True)
extract_model("oauth2_models.py.txt", "OAuth2DynamicClientRegistration",
              ["provider", "override_property_mappings"], ["PolicyBindingModel"], "seam_oauth2", meta=True)
extract_model("oauth2_models.py.txt", "DynamicClientRegistrationPropertyMapping",
              ["property_mapping", "dynamic_client_registration"],
              ["SimpleThroughModel"], "seam_oauth2", meta=True)

model_names = ("PolicyBindingModel", "PropertyMapping", "ScopeMapping", "Provider", "OAuth2Provider",
               "Source", "ProviderPropertyMapping", "SourceUserPropertyMapping",
               "SourceGroupPropertyMapping", "OAuth2DynamicClientRegistration",
               "DynamicClientRegistrationPropertyMapping")
with connection.schema_editor() as editor:
    for name in model_names:
        editor.create_model(namespace[name])
globals().update({name: namespace[name] for name in model_names})


def superuser_queryset(user, permission, queryset):
    assert user.is_superuser is True, "seam cannot qualify filtered evidence"
    assert isinstance(queryset, models.QuerySet)
    assert permission == f"{queryset.model._meta.app_label}.view_{queryset.model._meta.model_name}"
    return queryset


def noop_schema(**kwargs):
    return lambda function: function


# Execute all producer declarations unchanged. Only import wiring is replaced;
# DRF Serializer/fields, decorators, Response and Django manager behavior are real.
tree = ast.parse((FIXTURES / "used_by.py.txt").read_text())
producer = {**globals(), "PassiveSerializer": Serializer, "Request": object,
            "ObjectFilter": object, "extend_schema": noop_schema,
            "get_objects_for_user": superuser_queryset}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))],
                        type_ignores=[]), str(FIXTURES / "used_by.py.txt"), "exec"), producer)


def used_by(mapping, superuser=True):
    view = NS(get_object=lambda: ScopeMapping.objects.get(pk=mapping))
    return list(producer["UsedByMixin"].used_by(view, NS(user=NS(is_superuser=superuser))).data)




def references(pk, providers, foreign=()):
    """Produce evidence for fixture state using real persisted relations."""
    clear()
    mapping = ScopeMapping.objects.create(pm_uuid=pk, name="owned scope", expression="return {}",
                                          scope_name="jocasta.read", description="")
    for row in providers:
        provider = OAuth2Provider.objects.create(pk=row["pk"], name=row.get("name", "foreign provider"))
        if pk in row.get("property_mappings", []):
            provider.property_mappings.add(mapping)
    for relation in foreign:
        if relation == "dcr":
            provider = OAuth2Provider.objects.create(pk=9999, name="foreign DCR provider")
            dcr = OAuth2DynamicClientRegistration.objects.create(provider=provider)
            dcr.override_property_mappings.add(mapping)
        else:
            assert relation in ("source_user", "source_group")
            source = Source.objects.create(name=relation, slug=relation)
            manager = (source.user_property_mappings if relation == "source_user"
                       else source.group_property_mappings)
            manager.add(mapping)
    return used_by(pk)


def clear():
    # FK deletion order is real; deleting parents cascades through relevant rows.
    OAuth2DynamicClientRegistration.objects.all().delete()
    Source.objects.all().delete()
    Provider.objects.all().delete()
    PropertyMapping.objects.all().delete()
    PolicyBindingModel.objects.all().delete()
