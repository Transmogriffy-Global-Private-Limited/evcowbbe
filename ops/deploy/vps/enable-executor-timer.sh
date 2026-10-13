#!/usr/bin/env bash
# THIRD separately approved stage: enable unattended deployment only after
# single-shot positive-path acceptance and recovery validation.
set -euo pipefail
[[ $# == 1 && $1 =~ ^[0-9a-f]{40}$ && $EUID -eq 0 ]] || exit 2
sha=$1
root=/opt/evcowbbe-deploy
[[ $(readlink "$root/current") == "releases/$sha" ]] || exit 2
source=$root/releases/$sha/ops/deploy/vps
arm=$root/executor-arm
lock=$root/executor.lock
sudoers=/etc/sudoers.d/evcowbbe-executor
credential=/etc/evcowbbe-deploy/github-readonly.token
[[ -f $arm && ! -L $arm && $(stat -c %U:%a "$arm") == root:444 ]] || exit 2
[[ $(cat "$arm") == "evcowbbe-executor-v1 $sha" ]] || exit 2
[[ -f $lock && ! -L $lock && $(stat -c %U:%a "$lock") == root:444 ]] || exit 2
[[ -f $credential && ! -L $credential && $(stat -c %U:%a "$credential") == root:600 ]] || exit 2
[[ -s $credential && $(stat -c %s "$credential") -le 16384 ]] || exit 2
cmp "$source/sudoers/evcowbbe-executor" "$sudoers"
visudo -cf "$sudoers" >/dev/null
cmp "$source/systemd/evcowbbe-deploy-executor.service" /etc/systemd/system/evcowbbe-deploy-executor.service
cmp "$source/systemd/evcowbbe-deploy-executor.timer" /etc/systemd/system/evcowbbe-deploy-executor.timer
systemctl is-active --quiet evcowbbe-dev.service
if systemctl is-active --quiet evcowbbe-deploy-executor.service; then exit 2; fi
if systemctl is-active --quiet evcowbbe-deploy-executor.timer; then exit 2; fi
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
systemctl daemon-reload
systemctl enable --now evcowbbe-deploy-executor.timer
systemctl is-active --quiet evcowbbe-deploy-executor.timer
printf 'executor_timer_enabled_for_control_sha=%s\n' "$sha"
