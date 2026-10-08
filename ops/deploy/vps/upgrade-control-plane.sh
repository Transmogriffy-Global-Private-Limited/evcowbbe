#!/usr/bin/env bash
# Upgrade immutable *control-plane code only*, never reset SQLite, SSH or app.
# This does not install/enable any privileged executor, sudo rule or scheduler.
set -euo pipefail
usage() { echo 'usage: upgrade-control-plane.sh --source-dir DIR --source-sha SHA' >&2; exit 2; }
[[ $# -eq 4 ]] || usage
source_dir='' ; source_sha=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir) source_dir=$2 ;;
    --source-sha) source_sha=$2 ;;
    *) usage ;;
  esac
  shift 2
done
[[ $EUID -eq 0 && $source_sha =~ ^[0-9a-f]{40}$ ]] || usage
[[ -d $source_dir && ! -L $source_dir && -d $source_dir/ops/deploy/vps ]] || usage
for tool in git tar mktemp python3 runuser stat go; do command -v "$tool" >/dev/null || exit 2; done
[[ $(git -C "$source_dir" rev-parse --verify HEAD) == "$source_sha" ]] || { echo 'source SHA mismatch' >&2; exit 2; }
[[ -z $(git -C "$source_dir" status --porcelain --untracked-files=all) ]] || { echo 'source checkout dirty' >&2; exit 2; }
[[ $(stat -c '%U' "$source_dir") == root ]] || { echo 'source checkout not root-owned' >&2; exit 2; }
install_root=/opt/evcowbbe-deploy
release_root=$install_root/releases
current=$install_root/current
target=$release_root/$source_sha
state_dir=/var/lib/evcowbbe-deploy
[[ -L $current && -d $release_root && ! -L $release_root ]] || { echo 'installed control plane not found' >&2; exit 2; }
prior=$(readlink "$current")
[[ $prior =~ ^releases/[0-9a-f]{40}$ && -d $install_root/$prior ]] || { echo 'current control plane symlink unsafe' >&2; exit 2; }
[[ ! -e $target && ! -L $target ]] || { echo 'target release already exists: inspect, do not overwrite' >&2; exit 2; }
[[ -d $state_dir && ! -L $state_dir && $(stat -c '%U:%G' "$state_dir") == evcow-orchestrator:evcow-orchestrator ]] || { echo 'durable state owner mismatch' >&2; exit 2; }
[[ -f $state_dir/orchestrator.sqlite3 && ! -L $state_dir/orchestrator.sqlite3 ]] || { echo 'durable state missing' >&2; exit 2; }
[[ $(stat -c '%U:%G' "$state_dir/orchestrator.sqlite3") == evcow-orchestrator:evcow-orchestrator ]] || { echo 'durable state file ownership mismatch' >&2; exit 2; }
runuser -u evcow-orchestrator -- "$install_root/$prior/bin/evcowbbe-deploy-reconcile" >/dev/null
stage=''; next_link=''; published=0
cleanup() {
  code=$?
  trap - EXIT
  [[ -z $stage ]] || rm -rf -- "$stage"
  [[ -z $next_link ]] || rm -f -- "$next_link"
  if (( code != 0 && published == 1 )); then
    back="$install_root/.rollback-$$"
    ln -s -- "$prior" "$back"
    mv -Tf -- "$back" "$current" || true
  fi
  exit "$code"
}
trap cleanup EXIT
stage=$(mktemp -d "$release_root/.staging-$source_sha-XXXXXXXX")
# Bundle the dedicated observer with the root-owned control-plane release.
# This observer is independent of both the currently running and candidate
# application's migration binaries. Build from the reviewed exact Git objects.
git -C "$source_dir" archive --format=tar "$source_sha" \
  ops cmd/ledger-observer internal/database go.mod go.sum | tar -xf - -C "$stage"
for f in "$stage"/ops/deploy/vps/bin/*; do chmod 0755 "$f"; done
find "$stage" -type d -exec chmod 0755 {} +
find "$stage" -type f ! -path '*/bin/*' -exec chmod 0644 {} +
ln -s ops/deploy/vps/bin "$stage/bin"
chown -R root:root "$stage"
python3 -m compileall -q "$stage/ops/deploy"
# No network dependency fetching or toolchain upgrades as root.
(
  cd "$stage"
  env GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOWORK=off GOFLAGS= \
    go build -trimpath -buildvcs=false -o \
    "$stage/ops/deploy/vps/bin/evcowbbe-ledger-observer" ./cmd/ledger-observer
)
chmod 0555 "$stage/ops/deploy/vps/bin/evcowbbe-ledger-observer"
[[ -x $stage/bin/evcowbbe-deploy-ssh-ingress && -x $stage/bin/evcowbbe-deploy-reconcile && -x $stage/bin/evcowbbe-deploy-host-op && -x $stage/bin/evcowbbe-ledger-observer ]] || exit 2
mv -T -- "$stage" "$target"
stage=''
next_link="$install_root/.current-next-$$"
ln -s -- "releases/$source_sha" "$next_link"
mv -Tf -- "$next_link" "$current"
next_link=''
published=1
runuser -u evcow-orchestrator -- "$current/bin/evcowbbe-deploy-reconcile" >/dev/null
published=0
printf 'upgraded_control_plane_sha=%s\n' "$source_sha"
printf 'previous_control_plane_target=%s\n' "$prior"
printf '%s\n' 'No SQLite reset, SSH edits, sudoers edits, deployment, migration, or executor enablement performed'
