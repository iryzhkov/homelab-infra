#!/usr/bin/env bash
#
# Route a Forgejo webhook to the handler for its repository.
#
# Called by the webhook daemon as: dispatch.sh <event> <owner/repo> <ref>
# A handler is handlers/<owner>/<repo>.sh, run with the same three arguments
# and with FORGEJO_EVENT, FORGEJO_REPO and FORGEJO_REF in its environment. It
# decides for itself which events and branches it cares about. A repository
# with no handler is silently ignored, which is what makes a system-wide
# webhook safe: every push arrives here, most of them mean nothing.
#
# Runs of the same handler are serialised with a lock, so two pushes in quick
# succession build twice rather than concurrently; a push that arrives during
# a build is not lost.

set -euo pipefail

event="${1:-}"
repo="${2:-}"
ref="${3:-}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
handler="$here/handlers/$repo.sh"

log() { printf "dispatch: %s\n" "$*"; }

case "$repo" in
  */*) ;;
  *) log "malformed repository '$repo'"; exit 0 ;;
esac

if [ ! -x "$handler" ]; then
  log "$event on $repo $ref: no handler"
  exit 0
fi

log "$event on $repo $ref: running $handler"
export FORGEJO_EVENT="$event" FORGEJO_REPO="$repo" FORGEJO_REF="$ref"
exec flock -w 600 "$handler" "$handler" "$event" "$repo" "$ref"
