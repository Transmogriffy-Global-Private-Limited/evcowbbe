#!/usr/bin/env bash
# Explicit root-only setup for incident attestation storage; no deployment.
set -euo pipefail
[[ $# -eq 0 && $EUID -eq 0 ]] || exit 2
root=/opt/evcowbbe-deploy
out=$root/recovery-attestations
[[ -d "$root" && ! -L "$root" && $(stat -c '%U' "$root") == root ]] || exit 2
[[ $(stat -c '%a' "$root") =~ ^(700|750|755)$ ]] || exit 2
[[ $(systemctl is-enabled evcowbbe-deploy-executor.timer 2>/dev/null || :) == disabled ]] || exit 2
[[ $(systemctl is-active evcowbbe-deploy-executor.timer 2>/dev/null || :) == inactive ]] || exit 2
[[ $(systemctl show -P MainPID evcowbbe-deploy-executor.service) == 0 ]] || exit 2
[[ $(systemctl show -P ControlPID evcowbbe-deploy-executor.service) == 0 ]] || exit 2
lock="$root/executor.lock"
[[ -f "$lock" && ! -L "$lock" && $(stat -c '%U:%a' "$lock") == root:444 ]] || exit 2
exec 9<"$lock"
flock -n -x 9 || exit 75
if [[ ! -e "$out" && ! -L "$out" ]]; then
  install -d -o root -g root -m 0700 -- "$out"
fi
[[ -d "$out" && ! -L "$out" && $(stat -c '%U:%G:%a' "$out") == root:root:700 ]] || exit 2
printf 'recovery_attestation_directory=%s\n' "$out"
printf '%s\n' 'NO SQLite modification, PostgreSQL write, executor enablement or deployment'
