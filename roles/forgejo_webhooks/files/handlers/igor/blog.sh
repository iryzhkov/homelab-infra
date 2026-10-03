#!/usr/bin/env bash
#
# Rebuild the blog when its main branch is pushed.
#
# The build itself is the blog repository's own deploy/build.sh, so a change to
# how the site is built is a commit there, not here. This script pulls, hands
# off, as the hugo-webhook binary did before it, and then publishes the built
# site to every Swarm worker.
#
# Since phase 5 the blog is served by the Swarm stack `hugo`, one nginx replica
# per worker, from /srv/swarm/hugo/public on each (stacks/hugo/stack.yml). The
# build still lands in the checkout's public/, which the Compose project served
# and which stays the source; from there it is copied to this host's
# /srv/swarm/hugo/public and to the other worker's, by the SSH alias this user
# has for it. Until the stack is deployed that directory does not exist and
# nothing is copied. A failed copy is logged and makes the handler fail, but
# does not stop the others; the next push, or the next deploy of the stack
# (stacks/hugo/swarm-prepare.yml), catches the copy up.

set -euo pipefail

[ "${FORGEJO_EVENT:-}" = push ] || exit 0
[ "${FORGEJO_REF:-}" = refs/heads/main ] || exit 0

blog="$HOME/app/hugo/blog"
served=/srv/swarm/hugo/public
mirrors=("$served" "normandy:$served")

log() { printf "%s blog: %s\n" "$(date --iso-8601=seconds)" "$*"; }

log "pulling"
git -C "$blog" pull --ff-only
log "building"
"$blog/deploy/build.sh"

status=0
if [ -d "$served" ]; then
  for target in "${mirrors[@]}"; do
    log "publishing to $target"
    if ! rsync -a --delete "$blog/public/" "$target/"; then
      log "publishing to $target failed"
      status=1
    fi
  done
fi
log "done"
exit "$status"
