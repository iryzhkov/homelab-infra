#!/usr/bin/env python3
"""Read-only storage preflight for the disabled objectstore (Garage) source.

Exit 0 means every check below passed at the moment it ran. It is a startup
assertion, not fencing: nothing watches the mounts afterwards, so a mount
lost or replaced after this returns is not detected (time of check to time
of use). Physical caps and the live durability gates are operator evidence
references that this script requires but cannot measure. Explicit budget
acceptance permits unquotaed stores; it enforces no physical allocation limit.

The script never creates, mounts, chowns or writes anything. It reads the
configuration, lstat()s path components, lists a mount root when a
whole-mount cap is claimed, and asks findmnt which mount holds each path.
See docs/objectstore-storage.md.

Exit codes: 0 passed, 2 refused, 3 disabled (nothing inspected).

    garage-storage-preflight.py --config storage.json
    garage-storage-preflight.py --config - < storage.json
    garage-storage-preflight.py --config storage.json --digest
"""
import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys

NFS = ("nfs", "nfs4")
LOCAL = ("ext4", "xfs", "btrfs")
# Mechanisms that bound physical allocation. A dedicated device or filesystem
# bounds a whole mount; a hard directory or project quota bounds one
# directory. Free space, Garage bucket quotas and cleanup scans are not caps.
WHOLE_MOUNT_CAPS = ("dedicated_volume", "dedicated_filesystem")
DIRECTORY_CAPS = ("nas_directory_hard_quota", "filesystem_project_hard_quota")
DURABILITY_GATES = ("full_volume", "mount_loss", "consistent_restore",
                    "fresh_client_roundtrip", "last_copy_policy")
LIMITATION = ("point-in-time startup check only: no continuous mount-loss "
              "enforcement or runtime fencing; caps and durability gates are "
              "evidence references, not measured here")


class Refusal(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def positive(value):
    return type(value) is int and value > 0


class HostProbe:
    """Everything the preflight reads from the host. Tests substitute it."""

    def lstat(self, path):
        return os.lstat(path)

    def listdir(self, path):
        return os.listdir(path)

    def mount(self, path):
        result = subprocess.run(
            ["findmnt", "--json", "--list", "--output",
             "TARGET,SOURCE,FSTYPE,FSROOT,OPTIONS", "--target", path],
            capture_output=True, text=True, timeout=10, check=False)
        require(result.returncode == 0, "findmnt could not identify the mount of " + path)
        mounts = json.loads(result.stdout).get("filesystems", [])
        require(len(mounts) == 1, "ambiguous mount for " + path)
        return mounts[0]


def digest(config):
    """Identify the exact storage/fsync configuration a qualification covers."""
    storage = {key: value for key, value in config.items() if key != "qualifications"}
    raw = json.dumps(storage, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical(value, what):
    require(isinstance(value, str) and value.startswith("/") and value != "/"
            and "//" not in value and os.path.normpath(value) == value,
            what + " must be an absolute canonical path other than /")
    # Docker interpolates once in stack config and again in stack deploy.
    # Even a literal existing directory containing $ can change on that path.
    require("$" not in value, what + " must not contain Docker interpolation ($)")
    return value


def below(path, root):
    return path.startswith(root + "/")


def expected_store(config, label):
    """Validate one store's declared identity without touching the host."""
    store = config.get(label)
    require(isinstance(store, dict), label + " storage is required")
    mapping = store.get("identity_mapping")
    if "identity_mapping" in store:
        require(label == "data", "identity_mapping is allowed only on data, never metadata")
        require(isinstance(mapping, dict)
                and set(mapping) == {"owner_uid", "owner_gid", "evidence"},
                "data identity_mapping requires exactly owner_uid, owner_gid and evidence")
        require(positive(mapping["owner_uid"]) and positive(mapping["owner_gid"]),
                "data identity_mapping needs positive non-root integer owner_uid and owner_gid")
        require(text(mapping["evidence"]),
                "data identity_mapping qualification evidence reference is required")
    mount = store.get("mount")
    require(isinstance(mount, dict), label + " mount identity is required")
    for key in ("target", "source", "fstype", "fsroot"):
        require(text(mount.get(key)), label + " mount " + key + " is required")
    root = canonical(mount["target"], label + " mount target")
    path = canonical(store.get("path"), label + " path")
    require(mount["fsroot"] == "/", label + ": bind or subtree mounts are not qualified")
    options = mount.get("options")
    require(isinstance(options, list) and all(text(item) for item in options)
            and "rw" in options, label + " mount options must list rw explicitly")
    # The mount root is shared with whatever else lives there, and keeping the
    # store below it means a restart after an unmount meets a missing bind
    # source instead of silently writing to the underlying directory.
    require(below(path, root), label + " path must be strictly below its mount target")
    if label == "data":
        require(mount["fstype"] in NFS, "data must be on an explicit NFS mount")
        server, colon, export = mount["source"].partition(":")
        require(server and colon and export.startswith("/"),
                "data mount source must be server:/export")
        require(("hard" in options) != ("soft" in options),
                "data mount options must name exactly one of hard or soft")
        # A top-level directory is a sibling of other consumers' folders on a
        # shared export, never nested inside one of them.
        require("/" not in path[len(root) + 1:],
                "data path must be a top-level directory of its mount")
    else:
        require(mount["fstype"] in LOCAL, "metadata and journals require a local filesystem")
    # Omitted mode preserves the original hard-cap contract and its digest.
    # Budget acceptance is an explicit per-store opt-in, never a cap fallback.
    mode = store.get("acceptance_mode", "physical_cap")
    require(mode in ("physical_cap", "operational_budget"),
            label + " acceptance_mode must be physical_cap or operational_budget")
    if mode == "operational_budget":
        require(store.get("physical_cap") is None,
                label + " operational budget must not claim a physical cap")
        budget = store.get("operational_budget")
        require(isinstance(budget, dict) and set(budget) == {"bytes", "risk_acceptance"},
                label + " operational_budget requires exactly bytes and risk_acceptance")
        require(positive(budget["bytes"]),
                label + " operational budget needs positive aggregate bytes")
        require(text(budget["risk_acceptance"]),
                label + " operational budget risk_acceptance evidence reference is required")
        return {"label": label, "mount": mount, "root": root, "path": path,
                "cap": None, "mode": mode, "budget": budget, "identity_mapping": mapping}
    require("operational_budget" not in store,
            label + " physical_cap mode must not declare an operational_budget")
    cap = store.get("physical_cap")
    require(isinstance(cap, dict), label + " physical cap is unqualified")
    require(positive(cap.get("bytes")) and positive(cap.get("inodes")),
            label + " physical cap needs positive bytes and inodes")
    require(text(cap.get("evidence")), label + " physical cap evidence is required")
    if cap.get("mechanism") in DIRECTORY_CAPS:
        require(cap.get("scope") == path,
                label + " directory hard quota must be scoped to exactly " + path)
    elif cap.get("mechanism") in WHOLE_MOUNT_CAPS:
        require("/" not in path[len(root) + 1:],
                label + " whole-mount cap requires a top-level store directory")
        require(cap.get("scope") == root,
                label + " whole-mount cap must be scoped to exactly " + root)
    else:
        raise Refusal(label + " cap mechanism must bound physical allocation ("
                      + ", ".join(WHOLE_MOUNT_CAPS + DIRECTORY_CAPS) + "); free space, "
                      "logical or bucket quotas and cleanup are not physical caps")
    return {"label": label, "mount": mount, "root": root, "path": path, "cap": cap,
            "mode": mode, "identity_mapping": mapping}


def inspect(probe, path, what):
    try:
        return probe.lstat(path)
    except OSError as error:
        raise Refusal(what + ": cannot inspect " + path + ": " + str(error.strerror))


def existing_directory(probe, path, what):
    """lstat every component: a symlink anywhere in the ancestry refuses."""
    parts = path.split("/")[1:]
    for index in range(1, len(parts) + 1):
        current = "/" + "/".join(parts[:index])
        info = inspect(probe, current, what)
        require(not stat.S_ISLNK(info.st_mode), what + " passes through a symlink at " + current)
    info = inspect(probe, path, what)
    require(stat.S_ISDIR(info.st_mode), what + " must be an existing directory: " + path)
    return info


def metadata_custody(probe, path, uid):
    """Check local directory custody, including /, before privileged rendering."""
    parts = path.split("/")[1:]
    directories = ["/"] + ["/" + "/".join(parts[:index])
                            for index in range(1, len(parts) + 1)]
    directories += [path + "/meta", path + "/config"]
    for index, directory in enumerate(directories):
        info = inspect(probe, directory, "metadata")
        require(not stat.S_ISLNK(info.st_mode),
                "metadata passes through a symlink at " + directory)
        require(stat.S_ISDIR(info.st_mode),
                "metadata must be an existing directory: " + directory)
        require(info.st_uid in (0, uid), "metadata: " + directory
                + " must be owned by root or uid " + str(uid))
        # A root-owned sticky strict ancestor protects its next trusted-owned
        # component from unlink/rename by other users. Never allow this at the
        # metadata parent or children: outsiders could plant garage.toml there.
        sticky_ancestor = (index < len(parts) and info.st_uid == 0
                           and info.st_mode & stat.S_ISVTX)
        require(not info.st_mode & 0o022 or sticky_ancestor,
                "metadata: " + directory + " must not be group or other writable"
                + " except at a root-owned sticky strict ancestor")


def check_mount(probe, path, mount, what):
    observed = probe.mount(path)
    require(isinstance(observed, dict), "invalid mount record for " + path)
    for key in ("target", "source", "fstype", "fsroot"):
        require(observed.get(key) == mount[key], "%s: mount %s of %s is %r, expected %r"
                % (what, key, path, observed.get(key), mount[key]))
    options = str(observed.get("options") or "").split(",")
    missing = [item for item in mount["options"] if item not in options]
    require(not missing, what + ": mount of " + path + " lacks " + ",".join(missing))
    return options


def probe_store(probe, store, uid):
    label, mount, root, path = store["label"], store["mount"], store["root"], store["path"]
    root_info = existing_directory(probe, root, label + " mount target")
    options = check_mount(probe, root, mount, label)
    # Data ancestry has separate NFS traversal/ACL/squash qualifications.
    # Privileged local metadata/config writes require trusted directory custody.
    if label == "metadata":
        metadata_custody(probe, path, uid)
    # Only data may have a qualified NFS owner mapping. Metadata keeps the
    # process uid, including the separate ancestor/config custody checks.
    mapping = store["identity_mapping"]
    if label == "data":
        checks = [(path, True)]
    else:
        checks = [(path, False), (path + "/meta", True), (path + "/config", False)]
    for directory, owned in checks:
        info = existing_directory(probe, directory, label)
        # findmnt and the device number must agree: an overmount or nested
        # filesystem below the root would redirect Garage to another device.
        check_mount(probe, directory, mount, label)
        require(info.st_dev == root_info.st_dev, label + ": " + directory
                + " is on another device than " + root)
        if owned:
            if mapping is not None:
                require(info.st_uid == mapping["owner_uid"]
                        and info.st_gid == mapping["owner_gid"],
                        label + ": " + directory + " must be owned by mapped uid:gid "
                        + str(mapping["owner_uid"]) + ":" + str(mapping["owner_gid"]))
                require(not info.st_mode & 0o077,
                        label + ": " + directory + " must have no group or other access")
            else:
                require(info.st_uid == uid and not info.st_mode & 0o077,
                        label + ": " + directory + " must be owned by uid " + str(uid)
                        + " with no group or other access")
    if store["cap"] is not None and store["cap"]["mechanism"] in WHOLE_MOUNT_CAPS:
        top = path[len(root) + 1:].split("/")[0]
        try:
            entries = set(probe.listdir(root))
        except OSError as error:
            raise Refusal(label + ": cannot list " + root + ": " + str(error.strerror))
        others = sorted(entries - {top, "lost+found"})
        require(not others, label + " mount is shared (also holds " + ", ".join(others[:5])
                + "); a whole-mount cap does not isolate the blob namespace")
    return options


def validate(config, probe=None):
    probe = probe or HostProbe()
    require(isinstance(config, dict), "configuration must be an object")
    require(type(config.get("enabled")) is bool, "enabled must be a boolean")
    if not config["enabled"]:
        # Disabled: no path, mount, host or qualification is inspected.
        return {"status": "disabled"}
    # Every declared fact is checked before the host is touched.
    require(text(config.get("host")), "explicit storage host is required")
    require("$" not in config["host"], "storage host must not contain Docker interpolation ($)")
    require(positive(config.get("uid")) and positive(config.get("gid")),
            "explicit non-root uid and gid are required")
    for key in ("data_fsync", "metadata_fsync"):
        require(type(config.get(key)) is bool, key + " must be a boolean")
    qualifications = config.get("qualifications")
    require(isinstance(qualifications, dict), "live qualifications are required")
    for key in DURABILITY_GATES:
        require(text(qualifications.get(key)), "unqualified live gate: " + key)
    require(qualifications.get("configuration") == digest(config),
            "qualifications must name this exact configuration digest")
    stores = [expected_store(config, label) for label in ("data", "metadata")]
    data, metadata = stores
    require(data["root"] != metadata["root"] and not below(data["path"], metadata["path"])
            and not below(metadata["path"], data["path"]),
            "data and metadata must be on separate mounts and disjoint paths")
    options = probe_store(probe, data, config["uid"])
    probe_store(probe, metadata, config["uid"])
    limitations = [LIMITATION]
    acceptances = {}
    for store in stores:
        acceptance = {"mode": store["mode"]}
        if store["mode"] == "operational_budget":
            acceptance["aggregate_budget_bytes"] = store["budget"]["bytes"]
        acceptances[store["label"]] = acceptance
    if data["mode"] == "operational_budget":
        limitations.append("operational data budget only, aggregate across artifacts and results: "
                           "no physical byte or inode enforcement, no reserved space, and no "
                           "multipart constraint; shared NAS exhaustion remains possible")
    if metadata["mode"] == "operational_budget":
        limitations.append("operational metadata budget only: no physical byte or inode "
                           "enforcement, no reserved space; other services and metadata can "
                           "exhaust the shared Docker filesystem; point-in-time check only, "
                           "not continuous admission or monitoring")
    if "soft" in options:
        limitations.append("soft NFS data mount: timeouts surface as I/O errors; "
                           "not last-copy durability evidence")
    return {
        "status": "passed-startup-preflight",
        "configuration": digest(config),
        "host": config["host"],
        "data_path": data["path"],
        "data_acceptance": acceptances["data"],
        "metadata_acceptance": acceptances["metadata"],
        "metadata_path": metadata["path"] + "/meta",
        "config_path": metadata["path"] + "/config",
        "limitations": limitations,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="JSON path, or - for stdin")
    parser.add_argument("--digest", action="store_true",
                        help="print the configuration digest and inspect nothing")
    args = parser.parse_args(argv)
    try:
        if args.config == "-":
            config = json.load(sys.stdin)
        else:
            with open(args.config, encoding="utf-8") as handle:
                config = json.load(handle)
        require(isinstance(config, dict), "configuration must be an object")
        if args.digest:
            print(digest(config))
            return 0
        result = validate(config)
    except (Refusal, OSError, ValueError, TypeError, subprocess.SubprocessError) as error:
        print("storage preflight refused: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 3 if result["status"] == "disabled" else 0


if __name__ == "__main__":
    sys.exit(main())
