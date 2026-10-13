#!/usr/bin/env bash
# SECOND explicit root operator authorization; does NOT start or enable executor.
# Refuse absent GitHub credential, active runs, unsafe templates or stale SHA.
set -euo pipefail
[[ $# == 1 && $1 =~ ^[0-9a-f]{40}$ && $EUID -eq 0 ]] || exit 2
sha=$1
root=/opt/evcowbbe-deploy
[[ $(readlink "$root/current") == "releases/$sha" ]] || exit 2
source=$root/releases/$sha/ops/deploy/vps
sudoers=/etc/sudoers.d/evcowbbe-executor
arm=/opt/evcowbbe-deploy/executor-arm
lock=$root/executor.lock
service=/etc/systemd/system/evcowbbe-deploy-executor.service
timer=/etc/systemd/system/evcowbbe-deploy-executor.timer
token=/etc/evcowbbe-deploy/github-readonly.token
for target in "$sudoers" "$arm"; do
  [[ ! -e $target && ! -L $target ]] || exit 2
done
[[ -f $token && ! -L $token && $(stat -c %U:%a "$token") == root:600 ]] || exit 2
[[ -s $token && $(stat -c %s "$token") -le 16384 ]] || exit 2
[[ -f $lock && ! -L $lock && $(stat -c %U:%a "$lock") == root:444 ]] || exit 2
cmp "$service" "$source/systemd/evcowbbe-deploy-executor.service"
cmp "$timer" "$source/systemd/evcowbbe-deploy-executor.timer"
[[ $(stat -c %U:%a "$service") == root:644 && $(stat -c %U:%a "$timer") == root:644 ]] || exit 2
systemctl is-active --quiet evcowbbe-dev.service
# Explicit preflight: live control plane must be in quiescent accepted state.
PYTHONPATH=$root/current python3 -B - /var/lib/evcowbbe-deploy/orchestrator.sqlite3 <<'PY'
import sys
from ops.deploy.control_plane import ControlPlane
s = ControlPlane(sys.argv[1]).status(verbose=True)
assert s['schema_version'] == 4
assert not [r for r in s['runs'] if r['status'] in {
  'planned', 'running', 'migration_boundary_claimed',
  'migration_uncertain', 'migration_committed'}]
assert not s['active_controls']
PY
visudo -cf "$source/sudoers/evcowbbe-executor" >/dev/null
if systemctl is-active --quiet evcowbbe-deploy-executor.timer; then exit 2; fi
if systemctl is-active --quiet evcowbbe-deploy-executor.service; then exit 2; fi
installed=0
armed=0
cleanup() {
  status=$?
  trap - EXIT
  if (( status != 0 )); then
    if (( armed )); then rm -f -- "$arm"; fi
    if (( installed )); then rm -f -- "$sudoers"; fi
    systemctl disable --now evcowbbe-deploy-executor.timer >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT
install -o root -g root -m 0440 "$source/sudoers/evcowbbe-executor" "$sudoers"
installed=1
visudo -cf "$sudoers" >/dev/null
( umask 077; printf 'evcowbbe-executor-v1 %s\n' "$sha" > "$arm" )
chown root:root "$arm"
chmod 0444 "$arm"
armed=1
systemctl daemon-reload
[[ $(systemctl is-enabled evcowbbe-deploy-executor.timer 2>/dev/null || :) != enabled ]] || exit 2
[[ $(systemctl is-active evcowbbe-deploy-executor.timer 2>/dev/null || :) != active ]] || exit 2
trap - EXIT
printf 'executor_armed_sha=%s\n' "$sha"
echo 'Executor sudo and arm installed; timer remains disabled, no execution triggered'
