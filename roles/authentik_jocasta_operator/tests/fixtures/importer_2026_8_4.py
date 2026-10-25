"""Exact importer methods, Authentik 2026.8.4, MIT licensed upstream.

https://github.com/goauthentik/authentik/blob/410f46e5cf18f97a1f0056b30984a145242df55d/authentik/blueprints/v1/importer.py
Only class scaffolding and this provenance note are added. Tests compile the AST
with explicit dependency stubs instead of importing the Authentik application.
"""
class Importer:
    def __update_pks_for_attrs(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Replace any value if it is a known primary key of an other object"""

        def updater(value) -> Any:
            if value in self.__pk_map:
                self.logger.debug("Updating reference in entry", value=value)
                return self.__pk_map[value]
            return value

        for key, value in attrs.items():
            try:
                if isinstance(value, dict):
                    for _, _inner_key in enumerate(value):
                        value[_inner_key] = updater(value[_inner_key])
                elif isinstance(value, list):
                    for idx, _inner_value in enumerate(value):
                        attrs[key][idx] = updater(_inner_value)
                else:
                    attrs[key] = updater(value)
            except TypeError:
                continue
        return attrs

    def __query_from_identifier(self, attrs: dict[str, Any]) -> Q:
        """Generate an or'd query from all identifiers in an entry"""
        # Since identifiers can also be pk-references to other objects (see FlowStageBinding)
        # we have to ensure those references are also replaced
        main_query = Q()
        if "pk" in attrs:
            main_query = Q(pk=attrs["pk"])
        sub_query = Q()
        for identifier, value in attrs.items():
            if identifier == "pk":
                continue
            if isinstance(value, dict):
                sub_query &= Q(**{f"{identifier}__contains": value})
            else:
                sub_query &= Q(**{identifier: value})

        return main_query | sub_query

    def _validate_single(self, entry: BlueprintEntry) -> BaseSerializer | None:  # noqa: PLR0915
        """Validate a single entry"""
        if not entry.check_all_conditions_match(self._import):
            self.logger.debug("One or more conditions of this entry are not fulfilled, skipping")
            return None

        model_app_label, model_name = entry.get_model(self._import).split(".")
        try:
            model: type[SerializerModel] = registry.get_model(model_app_label, model_name)
        except LookupError as exc:
            raise EntryInvalidError.from_entry(exc, entry) from exc
        # Don't use isinstance since we don't want to check for inheritance
        if not is_model_allowed(model):
            raise EntryInvalidError.from_entry(f"Model {model} not allowed", entry)
        if issubclass(model, BaseMetaModel):
            serializer_class: type[Serializer] = model.serializer()
            serializer = serializer_class(
                data=entry.get_attrs(self._import),
                context={
                    SERIALIZER_CONTEXT_BLUEPRINT: entry,
                },
            )
            try:
                serializer.is_valid(raise_exception=True)
            except ValidationError as exc:
                raise EntryInvalidError.from_entry(
                    f"Serializer errors {serializer.errors}",
                    validation_error=exc,
                    entry=entry,
                ) from exc
            return serializer

        # If we try to validate without referencing a possible instance
        # we'll get a duplicate error, hence we load the model here and return
        # the full serializer for later usage
        # Because a model might have multiple unique columns, we chain all identifiers together
        # to create an OR query.
        updated_identifiers = self.__update_pks_for_attrs(entry.get_identifiers(self._import))
        for key, value in list(updated_identifiers.items()):
            if isinstance(value, dict) and "pk" in value:
                del updated_identifiers[key]
                updated_identifiers[f"{key}"] = value["pk"]

        query = self.__query_from_identifier(updated_identifiers)
        if not query:
            raise EntryInvalidError.from_entry("No or invalid identifiers", entry)

        try:
            existing_models = model.objects.filter(query)
        except FieldError as exc:
            raise EntryInvalidError.from_entry(f"Invalid identifier field: {exc}", entry) from exc

        serializer_kwargs = {}
        model_instance = existing_models.first()
        override_serializer_instance = False
        if (
            not isinstance(model(), BaseMetaModel)
            and model_instance
            and entry.state != BlueprintEntryDesiredState.MUST_CREATED
        ):
            self.logger.debug(
                "Initialize serializer with instance",
                model=model,
                instance=model_instance,
                pk=model_instance.pk,
            )
            serializer_kwargs["instance"] = model_instance
            serializer_kwargs["partial"] = True
        elif model_instance and entry.state == BlueprintEntryDesiredState.MUST_CREATED:
            msg = (
                f"State is set to {BlueprintEntryDesiredState.MUST_CREATED.value} "
                "and object exists already",
            )
            raise EntryInvalidError.from_entry(
                ValidationError({k: msg for k in entry.identifiers.keys()}, "unique"),
                entry,
            )
        else:
            self.logger.debug(
                "Initialized new serializer instance",
                model=model,
                **cleanse_dict(updated_identifiers),
            )
            override_serializer_instance = True
        try:
            full_data = self.__update_pks_for_attrs(entry.get_attrs(self._import))
        except ValueError as exc:
            raise EntryInvalidError.from_entry(exc, entry) from exc
        always_merger.merge(full_data, updated_identifiers)
        serializer_kwargs["data"] = full_data

        serializer: Serializer = model().serializer(
            context={
                SERIALIZER_CONTEXT_BLUEPRINT: entry,
            },
            **serializer_kwargs,
        )
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as exc:
            raise EntryInvalidError.from_entry(
                f"Serializer errors {serializer.errors}",
                validation_error=exc,
                entry=entry,
                serializer=serializer,
            ) from exc
        if override_serializer_instance:
            model_instance = model()
            # pk needs to be set on the model instance otherwise a new one will be generated
            if "pk" in updated_identifiers:
                model_instance.pk = model._meta.pk.to_python(updated_identifiers["pk"])
            serializer.instance = model_instance
        return serializer
