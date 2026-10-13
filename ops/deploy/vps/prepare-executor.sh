#!/usr/bin/env bash
# Explicit root-only preparation: installs DISABLED executor service templates.
# Never installs sudo privileges, arms execution, enables timers or changes apps.
set -euo pipefail
[[ $# == 1 && $1 =~ ^[0-9a-f]{40}$ && $EUID -eq 0 ]] || exit 2
sha=$1
root=/opt/evcowbbe-deploy
release=$root/releases/$sha
[[ $(readlink "$root/current") == "releases/$sha" ]] || exit 2
[[ -d $release && ! -L $release && $(stat -c %U "$release") == root ]] || exit 2
[[ $(stat -c %U:%a "$root") == root:755 ]] || exit 2
for user in evcow-orchestrator evcow-builder evcowbbe evcow-trigger; do id "$user" >/dev/null; done
[[ -f /var/lib/evcowbbe-deploy/orchestrator.sqlite3 ]] || exit 2
app_sha=$(readlink -f /srv/evcowbbe/current)
[[ $app_sha =~ ^/srv/evcowbbe/releases/[0-9a-f]{40}$ ]] || exit 2
systemctl is-active --quiet evcowbbe-dev.service
systemctl is-active --quiet evcowbbe-deploy-reconcile.timer
sudoers=/etc/sudoers.d/evcowbbe-executor
service=/etc/systemd/system/evcowbbe-deploy-executor.service
timer=/etc/systemd/system/evcowbbe-deploy-executor.timer
arm=/opt/evcowbbe-deploy/executor-arm
lock=$root/executor.lock
for dest in "$sudoers" "$service" "$timer" "$arm" "$lock"; do
  [[ ! -e $dest && ! -L $dest ]] || { echo 'executor artifact exists; stop and inspect' >&2; exit 2; }
done
source=$release/ops/deploy/vps
python3 - "$source/bin/evcowbbe-deploy-execute" "$source/bin/evcowbbe-deploy-recovery-inspect" <<'PYAST'
import ast
from pathlib import Path
import sys
for name in sys.argv[1:]:
    ast.parse(Path(name).read_text(encoding='utf-8'), filename=name)
PYAST
visudo -cf "$source/sudoers/evcowbbe-executor" >/dev/null
[[ -f $source/systemd/evcowbbe-deploy-executor.service && -f $source/systemd/evcowbbe-deploy-executor.timer ]] || exit 2
[[ $(stat -c %U "$source/bin/evcowbbe-deploy-execute") == root ]] || exit 2
created=()
cleanup() {
  status=$?
  trap - EXIT
  if (( status != 0 )); then
    for path in "${created[@]}"; do rm -f -- "$path"; done
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT
install -o root -g root -m 0444 /dev/null "$lock"
created+=("$lock")
install -o root -g root -m 0644 "$source/systemd/evcowbbe-deploy-executor.service" "$service"
created+=("$service")
install -o root -g root -m 0644 "$source/systemd/evcowbbe-deploy-executor.timer" "$timer"
created+=("$timer")
systemctl daemon-reload
[[ $(systemctl is-enabled evcowbbe-deploy-executor.timer 2>/dev/null || :) != enabled ]] || exit 2
[[ $(systemctl is-active evcowbbe-deploy-executor.timer 2>/dev/null || :) != active ]] || exit 2
trap - EXIT
printf 'executor_prepared_sha=%s\n' "$sha"
echo 'Disabled systemd templates and root-owned lock installed; no sudoers or arm created'
