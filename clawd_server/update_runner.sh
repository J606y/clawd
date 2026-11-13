#!/bin/sh
# Clawd panel update runner — runs detached (systemd transient unit) so it
# survives the panel restart it performs.
# Usage: runner.sh <run-dir> <tag-commit> <old-head>
# Env: CLAWD_PANEL_UNIT (default clawd-panel-accept),
# CLAWD_PANEL_HEALTH_URL (default http://127.0.0.1:39231/clawd/ready).
# The caller (clawd_server/update_native.py, UPDATE_SH) writes our exit code
# to <run-dir>/exit-code. Any failure after the reset rolls the code and the
# panel dist back to <old-head> and restarts the panel again.
set -u
RUN_DIR=${1:?}
TAG_COMMIT=${2:?}
OLD_HEAD=${3:?}
UNIT=${CLAWD_PANEL_UNIT:-clawd-panel-accept}
HEALTH_URL=${CLAWD_PANEL_HEALTH_URL:-http://127.0.0.1:39231/clawd/ready}
REPO=$(pwd)
DIST=apps/panel/dist
STAGED="$RUN_DIR/dist.new"
OLD_DIST="$RUN_DIR/dist.old"
log() { printf '[runner] %s\n' "$*"; }
wait_ready() {
  i=0
  tries=${CLAWD_UPDATE_HEALTH_TRIES:-60}
  while [ "$i" -lt "$tries" ]; do
    if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
      log "panel ready: $HEALTH_URL"
      return 0
    fi
    i=$((i + 1))
    sleep 2
  done
  log "panel did not become ready: $HEALTH_URL"
  return 1
}
rollback() {
  log "rolling back to $OLD_HEAD"
  git -c safe.directory='*' reset --hard "$OLD_HEAD" || true
  if [ -d "$OLD_DIST" ]; then
    rm -rf "$DIST"
    mv "$OLD_DIST" "$DIST"
  fi
  systemctl restart "$UNIT" || true
  wait_ready || true
}
trap 'log "interrupted; rolling back"; rollback; exit 1' INT TERM
[ -d "$REPO/.git" ] || { log "not a git repo: $REPO"; exit 1; }
log "resetting to $TAG_COMMIT"
git -c safe.directory='*' reset --hard "$TAG_COMMIT" || { log "git reset failed"; rollback; exit 1; }
if [ -f apps/panel/package.json ]; then
  log "building panel"
  if (cd apps/panel && npm run build -- --outDir "$STAGED"); then
    if [ ! -f "$STAGED/index.html" ]; then
      log "build produced no index.html"
      rollback
      exit 1
    fi
    if [ -d "$DIST" ]; then
      mv "$DIST" "$OLD_DIST" || { log "could not move old dist aside"; rollback; exit 1; }
    fi
    if mv "$STAGED" "$DIST"; then
      log "dist swapped"
    else
      log "dist swap failed; restoring old dist"
      [ -d "$OLD_DIST" ] && mv "$OLD_DIST" "$DIST"
      rollback
      exit 1
    fi
  else
    log "panel build failed"
    rollback
    exit 1
  fi
else
  log "no panel package.json; skipping build"
fi
log "restarting $UNIT"
systemctl restart "$UNIT" || { log "restart failed"; rollback; exit 1; }
if ! wait_ready; then
  log "health check failed after restart"
  rollback
  exit 1
fi
log "update complete"
exit 0
