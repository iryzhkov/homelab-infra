"""Isolated real Django/DRF creation seam for the pinned blueprint importer.

Only the importer's three creation/lookup methods are executed. Authentik's
application registry and logging are stubbed; Django inheritance, serializers,
and SQLite persistence are real. No production service or credential is used.
"""
import ast
import os
from copy import deepcopy
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
import unittest
from uuid import UUID, uuid4

home = Path(os.environ["HOME"]).resolve()
tmp = Path(os.environ["TMPDIR"]).resolve()
assert home == Path(os.environ["MACHINE_IDENTITY_TEST_HOME"]).resolve()
assert tmp.is_relative_to(home) and tmp != home and not tmp.is_relative_to("/tmp")

from django.conf import settings
if not settings.configured:
    settings.configure(INSTALLED_APPS=[], DATABASES={"default": {
        "ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        SECRET_KEY="isolated-test-only")
import django
django.setup()
from django.core.exceptions import FieldError
from django.db import connection, models
from django.db.models import Q
from deepmerge import always_merger
from rest_framework.exceptions import ValidationError
from rest_framework.serializers import ModelSerializer, Serializer


class Parent(models.Model):
    pm_uuid = models.UUIDField(primary_key=True, editable=False, default=uuid4)
    name = models.TextField(unique=True)
    class Meta:
        app_label = "isolated"

    @property
    def serializer(self):
        return ParentSerializer


class ParentSerializer(ModelSerializer):
    class Meta:
        model = Parent
        fields = ["pk", "name"]


class Child(Parent):
    scope_name = models.TextField()
    class Meta:
        app_label = "isolated"

    @property
    def serializer(self):
        return ChildSerializer


class ChildSerializer(ModelSerializer):
    class Meta:
        model = Child
        fields = ["pk", "name", "scope_name"]


class BaseMetaModel:
    pass


class State(Enum):
    MUST_CREATED = "must_created"


class Entry:
    state = None
    def __init__(self, pk, attrs=None):
        self.identifiers = {"pk": pk}
        self.attrs = {"name": "synthetic mapping", "scope_name": "jocasta.read"} | (attrs or {})
    def check_all_conditions_match(self, blueprint):
        return True
    def get_model(self, blueprint):
        return "isolated.child"
    def get_identifiers(self, blueprint):
        return deepcopy(self.identifiers)
    def get_attrs(self, blueprint):
        return deepcopy(self.attrs)


class EntryInvalidError(Exception):
    @classmethod
    def from_entry(cls, *args, **kwargs):
        return cls(str(args))


source = Path(__file__).with_name("fixtures").joinpath("importer_2026_8_4.py")
tree = ast.parse(source.read_text())
original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Importer")
methods = [n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in {
    "__update_pks_for_attrs", "__query_from_identifier", "_validate_single"}]
namespace = dict(Q=Q, Any=object, BaseSerializer=object, Serializer=Serializer,
    BlueprintEntry=Entry, Model=models.Model, SerializerModel=models.Model,
    BaseMetaModel=BaseMetaModel, BlueprintEntryDesiredState=State,
    registry=SimpleNamespace(get_model=lambda *_: Child),
    is_model_allowed=lambda _: True, FieldError=FieldError,
    ValidationError=ValidationError, EntryInvalidError=EntryInvalidError,
    always_merger=always_merger, cleanse_dict=lambda x: x,
    SERIALIZER_CONTEXT_BLUEPRINT="blueprint_entry")
compiled = ast.Module(body=[ast.ClassDef(name="Importer", bases=[], keywords=[],
    body=methods, decorator_list=[])], type_ignores=[])
ast.fix_missing_locations(compiled)
exec(compile(compiled, str(source), "exec"), namespace)
Importer = namespace["Importer"]


def create(pk, attrs=None, model=Child):
    namespace["registry"].get_model = lambda *_: model
    importer = Importer()
    importer._import = None
    importer._Importer__pk_map = {}
    importer.logger = SimpleNamespace(debug=lambda *a, **kw: None)
    serializer = importer._validate_single(Entry(pk, attrs))
    return serializer.save()


class ImporterSemanticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with connection.schema_editor() as editor:
            editor.create_model(Parent)
            editor.create_model(Child)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as editor:
            editor.delete_model(Child)
            editor.delete_model(Parent)

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM isolated_child')
            cursor.execute('DELETE FROM isolated_parent')

    def test_noninherited_uuid_model_preserves_declared_uuid(self):
        wanted = UUID("eab3de97-d783-511d-8477-6b4a904ec17c")
        self.assertEqual(create(str(wanted), model=Parent).pk, wanted)

    def test_current_contract_creation_preserves_declared_uuid(self):
        wanted = UUID("eab3de97-d783-511d-8477-6b4a904ec17c")
        obj = create(str(wanted))
        self.assertEqual(obj.pk, wanted, "Pinned importer creation replaced declared mapping UUID")

    def test_attrs_pk_does_not_fix_inherited_uuid_allocation(self):
        wanted = UUID("eab3de97-d783-511d-8477-6b4a904ec17c")
        obj = create(str(wanted), {"pk": str(wanted)})
        self.assertNotEqual(obj.pk, wanted)
        self.assertEqual(obj.pk, obj.pm_uuid)

    def test_allocated_identity_selects_existing_instance_without_replacement(self):
        obj = create(str(uuid4()))
        current = create(str(obj.pk))
        self.assertEqual(current.pk, obj.pk)
        self.assertEqual(Child.objects.count(), 1)
        self.assertEqual(Parent.objects.count(), 1)


if __name__ == "__main__":
    unittest.main()
