#!/usr/bin/env python3
"""Reconcile explicitly owned Emby rows through the existing protected API client."""
import copy
import re


def validate_ownership(document, server_id, users, recipes):
    """Validate all profiles before the caller performs any mutation."""
    if not isinstance(document, dict) or set(document) != {"version", "server_id", "profiles"}:
        raise ValueError("Explicit versioned Emby home ownership document required")
    if type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("Unsupported Emby home ownership version")
    if not re.fullmatch(r"[0-9a-f]{32}", str(document["server_id"])) or document["server_id"] != server_id:
        raise ValueError("Home ownership belongs to another server")
    if not isinstance(document["profiles"], dict) or set(document["profiles"]) != set(users):
        raise ValueError("Home ownership must name exactly the configured profiles")
    user_ids = []
    for name, user in users.items():
        profile = document["profiles"][name]
        if not isinstance(profile, dict) or set(profile) != {"user_id", "rows"}:
            raise ValueError("Invalid home ownership profile")
        if not isinstance(profile["user_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", profile["user_id"]):
            raise ValueError("Invalid home ownership user ID")
        if profile["user_id"] != user["Id"]:
            raise ValueError("Home ownership belongs to another user")
        user_ids.append(profile["user_id"])
        validate_rows([], recipes, profile["rows"], require_present=False)
    if len(set(user_ids)) != len(user_ids):
        raise ValueError("Multiple profiles identify the same user")


def row_id(value):
    # IDs are opaque server values. bool is not an integer ID.
    if type(value) is int and value >= 0:
        return ("integer", value)
    if isinstance(value, str) and 1 <= len(value) <= 128 and all(33 <= ord(c) <= 126 for c in value):
        return ("string", value)
    raise ValueError("Invalid home row ID")


def validate_rows(current, recipes, owned, require_present=True):
    keys = [r.get("key") for r in recipes]
    if not keys or any(not isinstance(k, str) or not k for k in keys) or len(set(keys)) != len(keys):
        raise ValueError("Missing or duplicate recipe key")
    if not isinstance(owned, dict) or set(owned) != set(keys):
        raise ValueError("Explicit row ownership must cover exactly the recipe keys")
    owned_ids = [row_id(owned[k]) for k in keys]
    if len(set(owned_ids)) != len(owned_ids):
        raise ValueError("Duplicate owned home row ID")
    actual = [row_id(r.get("Id")) for r in current]
    if len(set(actual)) != len(actual):
        raise ValueError("Duplicate server home row ID requires operator reconciliation")
    if require_present and not set(owned_ids).issubset(actual):
        raise ValueError("Owned home row missing; explicit operator adoption required")
    return keys, owned_ids


def plan_rows(current, targets, owned):
    """Reorder baseline IDs within baseline slots; preserve every other row."""
    keys, owned_ids = validate_rows(current, targets, owned)
    owned_set = set(owned_ids)
    by_id = {row_id(r["Id"]): r for r in current}
    ordered = []
    for key, target in zip(keys, targets):
        body = copy.deepcopy(by_id[row_id(owned[key])])
        body.update({k: copy.deepcopy(v) for k, v in target.items() if k != "key"})
        body["Id"] = owned[key]
        ordered.append(body)
    pending = iter(ordered)
    return [next(pending) if row_id(row["Id"]) in owned_set else copy.deepcopy(row) for row in current]


def main(api=None, desired=None, ownership=None, apply=None, backup_directory="/var/backups/emby-experience"):
    import copy, importlib.util, json, os, tempfile
    from pathlib import Path
    from importlib.machinery import SourceFileLoader
    if desired is None:
        desired = json.loads(os.environ["EMBY_EXPERIENCE_CONFIG"])
    if ownership is None:
        ownership = json.loads(os.environ["EMBY_EXPERIENCE_OWNERSHIP"])
    if apply is None:
        apply = os.environ["EMBY_EXPERIENCE_APPLY"] == "true"
    if api is None:
        spec = importlib.util.spec_from_loader("favorites", SourceFileLoader("favorites", "/usr/local/sbin/emby-favorite-upgrades"))
        if spec is None or spec.loader is None:
            raise RuntimeError("Existing Emby helper unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        api = module.clients(Path("/srv/swarm"))["emby"]
    users = api.call("Users")
    selected = {}
    for name in desired["profiles"]:
        candidates = [u for u in users if u["Name"] == name]
        if len(candidates) != 1:
            raise RuntimeError("Expected one named profile")
        selected[name] = candidates[0]
    validate_ownership(ownership, api.call("System/Info/Public")["Id"], selected, desired["sections"])
    libraries = api.call("Library/VirtualFolders")
    movies = [v for v in libraries if v["Name"] == "Movies"]
    if len(movies) != 1:
        raise RuntimeError("Expected one Movies library")
    before_other = {u["Id"]: copy.deepcopy(u["Configuration"]) for u in users if u["Name"] not in selected}
    changed = 0
    summaries = []
    def matches(actual, wanted):
        if isinstance(wanted, dict):
            return isinstance(actual, dict) and all((k in actual or v is None) and matches(actual.get(k), v) for k, v in wanted.items())
        return actual == wanted
    def snapshot(name, data):
        if not apply:
            return
        os.umask(0o077)
        directory = Path(backup_directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / (name + "-before.json")
        def validate():
            with path.open() as stream:
                saved = json.load(stream)
            if not isinstance(saved, dict) or set(saved) != set(data):
                raise RuntimeError("Invalid original snapshot")
            if "configuration" in data:
                valid = isinstance(saved["configuration"], dict) and isinstance(saved["sections"], list)
                valid = valid and all(isinstance(row, dict) and "Id" in row and "SectionType" in row for row in saved["sections"])
            else:
                valid = saved["Id"] == data["Id"] and isinstance(saved["LibraryOptions"], dict)
            if not valid:
                raise RuntimeError("Invalid original snapshot")
        if path.exists():
            validate()
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=".snapshot-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
            validate()
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    # Read and validate every profile and target before backups or any mutation.
    plans = {}
    originals = {}
    for name, user in selected.items():
        uid = user["Id"]
        current = api.call("Users/" + uid + "/HomeSections?isEditMode=true")
        originals[uid] = current
        views = api.call("Users/" + uid + "/Views?IncludeHidden=true&AllowDynamicChildren=false")["Items"]
        targets = []
        for recipe in desired["sections"]:
            section = {k: copy.deepcopy(v) for k, v in recipe.items() if k != "libraries"}
            section.setdefault("ItemTypes", [])
            section.setdefault("IncludeNextUpInResume", True)
            section.setdefault("ScrollDirection", "Horizontal")
            section.setdefault("SortOrder", "Ascending")
            section.setdefault("Query", {})
            allowed = recipe.get("libraries")
            if allowed:
                if any(sum(v["Name"] == n for v in views) != 1 for n in allowed):
                    raise RuntimeError("Missing or ambiguous library")
                section["ExcludedFolders"] = [v["Id"] for v in views if v["Name"] not in allowed]
            else:
                section["ExcludedFolders"] = []
            targets.append(section)
        plans[uid] = plan_rows(current, targets, ownership["profiles"][name]["rows"])
    for user in selected.values():
        snapshot(user["Id"], {"configuration": user["Configuration"], "sections": originals[user["Id"]]})
    snapshot("movies-library-options", {"Id": movies[0]["ItemId"], "LibraryOptions": movies[0]["LibraryOptions"]})
    for name, user in selected.items():
        uid = user["Id"]
        endpoint = "Users/" + uid + "/HomeSections"
        current = copy.deepcopy(originals[uid])
        original_config = copy.deepcopy(user["Configuration"])
        final_rows = plans[uid]
        owned_ids = {row_id(sid) for sid in ownership["profiles"][name]["rows"].values()}
        def fresh():
            if api.call(endpoint + "?isEditMode=true") != current:
                raise RuntimeError("Home rows changed concurrently; stop and re-preview")
        # Update only explicitly owned IDs. Unknown rows are never submitted.
        for target in final_rows:
            if row_id(target["Id"]) not in owned_ids:
                continue
            actual = next(s for s in current if row_id(s["Id"]) == row_id(target["Id"]))
            if not matches(actual, target):
                changed += 1
                if apply:
                    fresh()
                    api.call(endpoint, "POST", target)
                    readback = api.call(endpoint + "?isEditMode=true")
                    check = [s for s in readback if row_id(s["Id"]) == row_id(target["Id"])]
                    if len(check) != 1 or not matches(check[0], target) or not matches(target, check[0]):
                        raise RuntimeError("Owned home section did not round-trip")
                    unchanged_order = [s["Id"] for s in readback] == [s["Id"] for s in current]
                    other_before = [s for s in current if row_id(s["Id"]) != row_id(target["Id"])]
                    other_after = [s for s in readback if row_id(s["Id"]) != row_id(target["Id"])]
                    if not unchanged_order or other_after != other_before:
                        raise RuntimeError("Unrelated home row changed during update")
                    current = readback
        wanted_ids = [s["Id"] for s in final_rows]
        actual_ids = [s["Id"] for s in current]
        # First collect owned rows at the tail, then insert them into baseline
        # slots. Unknown rows are never moved directly or reordered relative to
        # each other. The extra moves occur only when baseline order drifts.
        moves = []
        simulated = list(actual_ids)
        if simulated != wanted_ids:
            for sid in (sid for sid in wanted_ids if row_id(sid) in owned_ids):
                index = len(simulated) - 1
                if simulated[index] != sid:
                    simulated.remove(sid)
                    simulated.append(sid)
                    moves.append((sid, index))
            for index, sid in enumerate(wanted_ids):
                if row_id(sid) in owned_ids and simulated[index] != sid:
                    simulated.remove(sid)
                    simulated.insert(index, sid)
                    moves.append((sid, index))
            if simulated != wanted_ids:
                raise RuntimeError("Cannot preserve unowned home placement")
        for sid, index in moves:
            changed += 1
            if apply:
                fresh()
                expected = copy.deepcopy(current)
                moving = next(s for s in expected if row_id(s["Id"]) == row_id(sid))
                expected.remove(moving)
                expected.insert(index, moving)
                api.call(endpoint + "/Move", "POST", {"ids": [sid], "newIndex": index})
                current = api.call(endpoint + "?isEditMode=true")
                if current != expected:
                    raise RuntimeError("Unexpected home row move or concurrent edit")
        config = dict(original_config)
        config.update(desired["playback"])
        if config != original_config:
            changed += 1
            if apply:
                api.call("Users/" + uid + "/Configuration", "POST", config)
        if apply:
            result = api.call("Users/" + uid)
            if result["Configuration"] != config:
                raise RuntimeError("Unexpected user preference drift")
            final = api.call(endpoint + "?isEditMode=true")
            if [s["Id"] for s in final] != wanted_ids:
                raise RuntimeError("Unexpected home-screen order")
            if [s for s in final if row_id(s["Id"]) not in owned_ids] != [s for s in originals[uid] if row_id(s["Id"]) not in owned_ids]:
                raise RuntimeError("Unexpected change to unowned home rows")
        summaries.append({"profile": name, "home_rows": len(final_rows), "resume_rewind_seconds": config["ResumeRewindSeconds"]})
    movie = movies[0]
    options = copy.deepcopy(movie["LibraryOptions"])
    expected_options = dict(options, ImportCollections=desired["importMovieCollections"])
    if options != expected_options:
        changed += 1
        if apply:
            api.call("Library/VirtualFolders/LibraryOptions", "POST", {"Id": movie["ItemId"], "LibraryOptions": expected_options})
            actual = [v for v in api.call("Library/VirtualFolders") if v["ItemId"] == movie["ItemId"]][0]["LibraryOptions"]
            if actual != expected_options:
                raise RuntimeError("Unexpected library option drift")
    if apply:
        after_other = {u["Id"]: u["Configuration"] for u in api.call("Users") if u["Id"] in before_other}
        if after_other != before_other:
            raise RuntimeError("Unexpected changes to another profile")
    print(json.dumps({"changed": changed, "mode": "apply" if apply else "check", "profiles": summaries, "native_movie_collections": desired["importMovieCollections"]}))


if __name__ == "__main__":
    main()
