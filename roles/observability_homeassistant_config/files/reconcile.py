#!/usr/bin/env python3
"""Reconcile only the approved HA Prometheus package and explicit include."""
import argparse, base64, datetime, json, pathlib, re, shlex, subprocess, sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-target", default="home-assistant")
    parser.add_argument("--package", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--maintenance", action="store_true")
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    def remote(command):
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", args.ssh_target, command],
                                capture_output=True, text=True, timeout=240)
        if result.returncode:
            raise RuntimeError("Home Assistant SSH command failed; remote output withheld")
        return result.stdout
    config = remote("cat /config/configuration.yaml")
    present = remote("test ! -f /config/packages/homelab_prometheus.yaml || cat /config/packages/homelab_prometheus.yaml")
    desired_package = pathlib.Path(args.package).read_text()
    include = "    homelab_prometheus: !include packages/homelab_prometheus.yaml"
    if len(re.findall(r"(?m)^homeassistant:[ \t]*$", config)) != 1:
        raise RuntimeError("Require exactly one existing homeassistant mapping")
    if len(re.findall(r"(?m)^  packages:[ \t]*$", config)) != 1:
        raise RuntimeError("Require exactly one explicit homeassistant packages mapping")
    if re.search(r"(?m)^prometheus:", config):
        raise RuntimeError("Refuse a separate top-level Prometheus integration")
    if "homelab_prometheus:" in config and include not in config:
        raise RuntimeError("Refuse a differently owned Prometheus include")
    home_match = re.search(r"(?ms)^homeassistant:[ \t]*\n(.*?)(?=^[^ \t#\n]|\Z)", config)
    if not home_match:
        raise RuntimeError("Require the existing explicit homeassistant block")
    home_block = home_match.group(1)
    if "  packages:\n" not in home_block or (include in config and include not in home_block):
        raise RuntimeError("Prometheus include must belong to homeassistant.packages")
    desired_config = config if include in config else config.replace("  packages:\n", "  packages:\n" + include + "\n", 1)
    changed = config != desired_config or present != desired_package
    result = {"changed": changed, "checked": args.check, "restart": False}
    if not changed or args.check:
        print(json.dumps(result)); return
    if not args.maintenance:
        raise RuntimeError("HA config change requires explicit maintenance=true; --check is read-only")
    remote("cd /config && git diff --quiet -- configuration.yaml packages/homelab_prometheus.yaml")
    baseline_head = remote("cd /config && git rev-parse HEAD").strip()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = "/backup/observability-config-" + stamp + ".tar"
    remote("umask 077; cd /config; " +
           "if test -f packages/homelab_prometheus.yaml; then tar -cf " + shlex.quote(backup) +
           " configuration.yaml packages/homelab_prometheus.yaml; else tar -cf " +
           shlex.quote(backup) + " configuration.yaml; fi && chmod 600 " + shlex.quote(backup))
    def write(path, content):
        encoded = base64.b64encode(content.encode()).decode()
        remote("umask 077; mkdir -p /config/packages; printf %s " + shlex.quote(encoded) +
               " | base64 -d > " + shlex.quote(path + ".observability.tmp") +
               " && chmod 644 " + shlex.quote(path + ".observability.tmp") +
               " && mv " + shlex.quote(path + ".observability.tmp") + " " + shlex.quote(path))
    try:
        write("/config/packages/homelab_prometheus.yaml", desired_package)
        write("/config/configuration.yaml", desired_config)
        remote("ha core check")
    except Exception:
        write("/config/configuration.yaml", config)
        if present:
            write("/config/packages/homelab_prometheus.yaml", present)
        else:
            remote("rm -f /config/packages/homelab_prometheus.yaml")
        raise RuntimeError("HA validation failed; the two owned configuration paths were restored, Core was not restarted")
    result["backup"] = backup
    result["baseline_git_head"] = baseline_head
    result["configuration_valid"] = True
    if args.restart:
        remote("ha core restart")
        result["restart"] = True
    print(json.dumps(result))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"failed": True, "reason": str(exc)}))
        sys.exit(1)
