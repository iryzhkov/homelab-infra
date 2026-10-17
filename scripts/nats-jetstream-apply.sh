#!/usr/bin/env bash
# Converge the CHALDEA account's JetStream streams and consumers to the JSON
# files under stacks/nats/jetstream/:
#
#   streams/<NAME>.json                 one stream each, in `nats stream add
#                                       --config` form; KV_<BUCKET> is the
#                                       stream behind key-value bucket <BUCKET>
#   consumers/<STREAM>/<durable>.json   one durable consumer each
#
# Run it from an operator machine whose nats CLI context authenticates as
# chaldea-admin (docs/chaldea-infra-foundation.md, "Deploy"):
#
#   NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh --check
#   NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh
#
# --check changes nothing: it names what is missing and prints the difference
# for what exists but differs. A real run creates what is missing and edits
# what differs, and touches nothing that already matches, so a second run
# changes nothing. Streams go before consumers. Nothing is ever deleted: a
# file removed here leaves its stream or consumer on the server, to be removed
# by hand once nothing uses it. NATS_BIN names the nats CLI when it is not on
# PATH. Default selection is AKASHA_EVENTS plus akasha-ingest only;
# --stream AKASHA_EVENTS and --all both select only this deployable subset. Selection is validated before any RPC.
#
# The files carry "metadata": {} and a consumer's "name" equal to its
# durable_name because that is how the server reports them back; without
# those the CLI reports a difference on every run.
set -euo pipefail

# Safe default: gateway-only v1. No parked work/quota definitions are present.
mode=apply
selection=AKASHA_EVENTS
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) mode=check; shift ;;
    --stream)
      [ "$#" -ge 2 ] || { echo "--stream needs a name" >&2; exit 2; }
      selection=$2; shift 2 ;;
    --all) selection=all; shift ;;
    *) echo "usage: $0 [--check] [--stream NAME | --all]" >&2; exit 2 ;;
  esac
done
case "$selection" in
  AKASHA_EVENTS|all) ;;
  *) echo "unknown stream: $selection" >&2; exit 2 ;;
esac

nats_bin=${NATS_BIN:-nats}
root="$(cd "$(dirname "$0")/.." && pwd)/stacks/nats/jetstream"

json_field() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$1" "$2"
}

listed() {
  grep -qxF "$1" <<<"$2"
}

# compare <stream|consumer> <names...> <file>: prints nothing and returns 0
# when the server matches the file, prints the difference and returns 1 when
# it does not, and stops the run on any other error. `edit --dry-run` exits
# 1 both for a difference and for an error, so the output tells them apart.
compare() {
  local kind=$1 output
  shift
  if output=$("$nats_bin" "$kind" edit "${@:1:$#-1}" --config "${!#}" --dry-run 2>&1); then
    [ -z "$output" ] && return 0
  fi
  if grep -q '^Differences' <<<"$output"; then
    printf '%s\n' "$output"
    return 1
  fi
  printf '%s\n' "$output" >&2
  exit 1
}

# The CLI refuses to edit a key-value bucket's stream without an interactive
# confirmation, even with --force, so a bucket is edited through `nats kv
# edit` with the same limits, which keeps the settings every bucket needs.
kv_edit() {
  python3 - "$1" <<'PY' | xargs -0 "$nats_bin" kv edit
import json, sys
c = json.load(open(sys.argv[1]))
args = [c["name"].removeprefix("KV_"),
        "--description=" + c["description"],
        "--history=%d" % c["max_msgs_per_subject"],
        "--ttl=%ds" % (c["max_age"] // 10**9),
        "--max-value-size=%d" % c["max_msg_size"],
        "--max-bucket-size=%d" % c["max_bytes"]]
sys.stdout.write("\0".join(args))
PY
}

# Existence is decided from the name lists, never from `info` failing: the
# CLI answers `info` for a missing name by trying to open an interactive
# picker, and a connection or permission error must not read as "missing".
# Each list is read as a plain assignment with `set -e` in force, so either
# kind of error stops the run there.
streams=$("$nats_bin" stream ls --names)

for file in "$root"/streams/*.json; do
  name=$(json_field "$file" name)
  [ "$selection" = all ] || [ "$selection" = "$name" ] || continue
  if ! listed "$name" "$streams"; then
    if [ "$mode" = check ]; then
      echo "stream $name: missing, a real run creates it"
    else
      "$nats_bin" stream add "$name" --config "$file"
    fi
  elif compare stream "$name" "$file"; then
    echo "stream $name: matches"
  elif [ "$mode" = apply ]; then
    case $name in
      KV_*) kv_edit "$file" ;;
      *) "$nats_bin" stream edit "$name" --config "$file" --force ;;
    esac
  fi
done

for file in "$root"/consumers/*/*.json; do
  stream=$(basename "$(dirname "$file")")
  [ "$selection" = all ] || [ "$selection" = "$stream" ] || continue
  name=$(json_field "$file" durable_name)
  if [ "$mode" = check ] && ! listed "$stream" "$streams"; then
    echo "consumer $stream/$name: stream missing, a real run creates both"
    continue
  fi
  consumers=$("$nats_bin" consumer ls "$stream" --names)
  if ! listed "$name" "$consumers"; then
    if [ "$mode" = check ]; then
      echo "consumer $stream/$name: missing, a real run creates it"
    else
      "$nats_bin" consumer add "$stream" "$name" --config "$file"
    fi
  elif compare consumer "$stream" "$name" "$file"; then
    echo "consumer $stream/$name: matches"
  elif [ "$mode" = apply ]; then
    "$nats_bin" consumer edit "$stream" "$name" --config "$file" --force
  fi
done
