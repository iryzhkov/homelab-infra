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


class PolicyParent(models.Model):
    policy_uuid = models.UUIDField(primary_key=True, editable=False, default=uuid4)
    name = models.TextField(unique=True)
    class Meta:
        app_label = "isolated"


class PolicyChild(PolicyParent):
    class Meta:
        app_label = "isolated"
    @property
    def serializer(self):
        return PolicySerializer


class PolicySerializer(ModelSerializer):
    class Meta:
        model = PolicyChild
        fields = ["pk", "name"]


class ApplicationParent(models.Model):
    pbm_uuid = models.UUIDField(primary_key=True, editable=False, default=uuid4)
    name = models.TextField(unique=True)
    class Meta:
        app_label = "isolated"


class ApplicationChild(ApplicationParent):
    class Meta:
        app_label = "isolated"
    @property
    def serializer(self):
        return ApplicationSerializer


class ApplicationSerializer(ModelSerializer):
    class Meta:
        model = ApplicationChild
        fields = ["pk", "pbm_uuid", "name"]


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
assert len(methods) == 3
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


def create(pk, attrs=None, model=Child, identifiers=None):
    namespace["registry"].get_model = lambda *_: model
    importer = Importer()
    importer._import = None
    importer._Importer__pk_map = {}
    importer.logger = SimpleNamespace(debug=lambda *a, **kw: None)
    entry = Entry(pk, attrs)
    if identifiers is not None:
        entry.identifiers = identifiers
    serializer = importer._validate_single(entry)
    return serializer.save()


class ImporterSemanticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with connection.schema_editor() as editor:
            editor.create_model(Parent)
            editor.create_model(Child)
            editor.create_model(PolicyParent)
            editor.create_model(PolicyChild)
            editor.create_model(ApplicationParent)
            editor.create_model(ApplicationChild)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as editor:
            editor.delete_model(ApplicationChild)
            editor.delete_model(ApplicationParent)
            editor.delete_model(PolicyChild)
            editor.delete_model(PolicyParent)
            editor.delete_model(Child)
            editor.delete_model(Parent)

    def tearDown(self):
        with connection.cursor() as cursor:
            for table in ('applicationchild', 'applicationparent', 'policychild', 'policyparent'):
                cursor.execute('DELETE FROM isolated_' + table)
            cursor.execute('DELETE FROM isolated_child')
            cursor.execute('DELETE FROM isolated_parent')

    def test_noninherited_uuid_model_preserves_declared_uuid(self):
        wanted = UUID("eab3de97-d783-511d-8477-6b4a904ec17c")
        self.assertEqual(create(str(wanted), model=Parent).pk, wanted)

    def test_current_contract_allocates_inherited_uuid(self):
        wanted = UUID("eab3de97-d783-511d-8477-6b4a904ec17c")
        obj = create(str(wanted))
        self.assertNotEqual(obj.pk, wanted)
        self.assertEqual(obj.pk, obj.pm_uuid)

    def test_parent_field_creation_probe(self):
        for model, field in ((Child, "pm_uuid"), (PolicyChild, "policy_uuid"),
                             (ApplicationChild, "pbm_uuid")):
            wanted = str(uuid4())
            obj = create(wanted, {field: wanted}, model,
                         {"pk": wanted, field: wanted})
            with self.subTest(field=field):
                self.assertNotEqual(str(obj.pk), wanted)
                self.assertEqual(obj.pk, getattr(obj, field))

    def test_attrs_pk_does_not_fix_inherited_uuid_allocation(self):
        wanted = UUID("eab3de97-d783-511d-8477-6b4a904ec17c")
        obj = create(str(wanted), {"pk": str(wanted)})
        self.assertNotEqual(obj.pk, wanted)
        self.assertEqual(obj.pk, obj.pm_uuid)

    def test_migration_targets_existing_inherited_rows_without_replacement(self):
        # Preexisting root-custodied parent identities; this never creates through
        # a corrected/forked importer. Ordinary native creation stays allocated.
        cases = ((Child, Parent, "pm_uuid", "7da2a648-fbd8-43bc-8517-3101b7b9b0d9"),
                 (PolicyChild, PolicyParent, "policy_uuid", "dc6e5831-77bb-4592-b57e-f160580ce466"),
                 (ApplicationChild, ApplicationParent, "pbm_uuid", "a3586abb-8f76-40b3-a6ca-dea7c8df0a02"))
        for model, parent, field, pk in cases:
            values = {field: UUID(pk), "name": "synthetic mapping"}
            if model is Child:
                values["scope_name"] = "jocasta.read"
            original = model.objects.create(**values)
            before = (model.objects.count(), parent.objects.count())
            for _ in range(2):
                updated = create(pk, model=model)
                self.assertEqual(updated.pk, original.pk)
                self.assertEqual(getattr(updated, field), original.pk)
                self.assertEqual(updated.name, original.name)
                if model is Child:
                    self.assertEqual(updated.scope_name, original.scope_name)
                self.assertEqual((model.objects.count(), parent.objects.count()), before)

    def test_allocated_identity_selects_existing_instance_without_replacement(self):
        obj = create(str(uuid4()))
        current = create(str(obj.pk))
        self.assertEqual(current.pk, obj.pk)
        self.assertEqual(Child.objects.count(), 1)
        self.assertEqual(Parent.objects.count(), 1)


if __name__ == "__main__":
    unittest.main()
