#!/usr/bin/env bash
#
# Rebuild the blog when its main branch is pushed.
#
# The build itself is the blog repository's own deploy/build.sh, so a change to
# how the site is built is a commit there, not here. This script only pulls
# and hands off, as the hugo-webhook binary did before it.

set -euo pipefail

[ "${FORGEJO_EVENT:-}" = push ] || exit 0
[ "${FORGEJO_REF:-}" = refs/heads/main ] || exit 0

blog="$HOME/app/hugo/blog"

log() { printf "%s blog: %s\n" "$(date --iso-8601=seconds)" "$*"; }

log "pulling"
git -C "$blog" pull --ff-only
log "building"
"$blog/deploy/build.sh"
log "done"
