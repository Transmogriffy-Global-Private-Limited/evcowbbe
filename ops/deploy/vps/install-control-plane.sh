#!/usr/bin/env bash
# Separately authorized root installation of PASSIVE ingress; no application deployment.
set -euo pipefail

usage() {
  printf '%s\n' 'usage: install-control-plane.sh --source-dir DIR --source-sha SHA --trigger-public-key-file FILE'
}
source_dir=''; source_sha=''; public_key_file=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; source_dir=$2; shift 2 ;;
    --source-sha) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; source_sha=$2; shift 2 ;;
    --trigger-public-key-file) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; public_key_file=$2; shift 2 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ $EUID -eq 0 ]] || { printf '%s\n' 'must run as root' >&2; exit 2; }
[[ $source_sha =~ ^[0-9a-f]{40}$ ]] || { printf '%s\n' 'source SHA must be 40 lowercase hexadecimal characters' >&2; exit 2; }
[[ -d $source_dir/ops/deploy/vps ]] || { printf '%s\n' 'source directory lacks the VPS package' >&2; exit 2; }
[[ -f $public_key_file && ! -L $public_key_file ]] || { printf '%s\n' 'trigger public key file is missing/unsafe' >&2; exit 2; }

# PRE-FLIGHT: perform all possible checks before creating any system artifact.
for command in git tar sshd ssh-keygen visudo runuser systemctl python3 cmp getent stat; do
  command -v "$command" >/dev/null || { printf 'missing required tool: %s\n' "$command" >&2; exit 2; }
done
[[ $(git -C "$source_dir" rev-parse --verify HEAD) == "$source_sha" ]] || {
  printf '%s\n' 'source checkout HEAD does not match the approved exact SHA' >&2; exit 2;
}
[[ -z $(git -C "$source_dir" status --porcelain --untracked-files=all) ]] || {
  printf '%s\n' 'source checkout is not clean (including untracked files)' >&2; exit 2;
}
git -C "$source_dir" cat-file -e "$source_sha^{commit}"
for account in evcow-trigger evcow-orchestrator; do
  id "$account" >/dev/null || { printf 'missing account: %s\n' "$account" >&2; exit 2; }
done
key_line=$(cat "$public_key_file")
[[ $(wc -l < "$public_key_file") -eq 1 && $key_line != *$'\r'* ]] || {
  printf '%s\n' 'public key must be exactly one LF-terminated line' >&2; exit 2;
}
read -r key_type key_blob _key_comment <<<"$key_line"
[[ $key_type == ssh-ed25519 && $key_blob =~ ^[A-Za-z0-9+/]+={0,2}$ ]] || {
  printf '%s\n' 'only a valid ssh-ed25519 trigger public key is accepted' >&2; exit 2;
}
ssh-keygen -lf "$public_key_file" >/dev/null

install_root=/opt/evcowbbe-deploy
release_root=$install_root/releases
release_dir=$release_root/$source_sha
state_dir=/var/lib/evcowbbe-deploy
sudoers_file=/etc/sudoers.d/evcowbbe-trigger-ingest
service_file=/etc/systemd/system/evcowbbe-deploy-reconcile.service
timer_file=/etc/systemd/system/evcowbbe-deploy-reconcile.timer
trigger_home=$(getent passwd evcow-trigger | cut -d: -f6)
[[ -n $trigger_home && -d $trigger_home && ! -L $trigger_home ]] || {
  printf '%s\n' 'trigger home is missing or symlinked' >&2; exit 2;
}
ssh_dir=$trigger_home/.ssh
authorized_keys=$ssh_dir/authorized_keys
[[ ! -e $authorized_keys && ! -L $authorized_keys ]] || {
  printf '%s\n' 'existing trigger authorized_keys was not modified; inspect first' >&2; exit 2;
}
[[ ! -e $ssh_dir && ! -L $ssh_dir ]] || {
  printf '%s\n' 'existing trigger .ssh directory was not modified; inspect first' >&2; exit 2;
}
for path in "$sudoers_file" "$service_file" "$timer_file" "$release_dir" "$install_root/current"; do
  [[ ! -e $path && ! -L $path ]] || { printf 'existing artifact was not modified: %s\n' "$path" >&2; exit 2; }
done
if [[ -e $state_dir || -L $state_dir ]]; then
  [[ ! -L $state_dir && -d $state_dir ]] || { printf '%s\n' 'state path is not a real directory' >&2; exit 2; }
  [[ $(stat -c '%U:%G' "$state_dir") == evcow-orchestrator:evcow-orchestrator ]] || {
    printf '%s\n' 'existing state directory must be owned by evcow-orchestrator; do not overwrite it' >&2; exit 2;
  }
  [[ $(stat -c '%a' "$state_dir") == 700 || $(stat -c '%a' "$state_dir") == 750 ]] || {
    printf '%s\n' 'existing state directory permissions are unsafe; inspect first' >&2; exit 2;
  }
fi
sshd -t
# The installer deliberately does NOT add a Match block to sshd_config.d:
# on Ubuntu, Include placement can make Match leak into global configuration.
# OpenSSH authorized_keys "restrict,command=..." is the enforced key policy.
effective=$(sshd -T -C user=evcow-trigger,host=localhost,addr=127.0.0.1)
grep -q '^pubkeyauthentication yes$' <<<"$effective" || {
  printf '%s\n' 'sshd does not permit public-key authentication for evcow-trigger' >&2; exit 2;
}
grep -Eq '^authorizedkeysfile .*([.]ssh/authorized_keys|%h/\.ssh/authorized_keys)' <<<"$effective" || {
  printf '%s\n' 'effective sshd AuthorizedKeysFile does not include the default trigger key path' >&2; exit 2;
}
grep -q '^authorizedkeyscommand none$' <<<"$effective" || {
  printf '%s\n' 'unexpected AuthorizedKeysCommand may authorize an unrestricted trigger key' >&2; exit 2;
}
grep -q '^trustedusercakeys none$' <<<"$effective" || {
  printf '%s\n' 'unexpected trusted SSH user CA may bypass per-key restrictions' >&2; exit 2;
}

# Validate committed source and configuration without executing untrusted worktree bytes.
release_stage=''
created=()
installed=0
on_exit() {
  local status=$?
  trap - EXIT
  if [[ $installed -ne 1 ]]; then
    if [[ ${#created[@]} -gt 0 ]]; then
      printf '%s\n' 'Installer failed; removing only artifacts created by this invocation' >&2
      for ((i=${#created[@]}-1; i>=0; i--)); do
        case "${created[i]}" in
          "$timer_file") systemctl disable --now evcowbbe-deploy-reconcile.timer >/dev/null 2>&1 || true; rm -f -- "$timer_file" ;;
          "$service_file"|"$sudoers_file"|"$authorized_keys"|"$install_root/current") rm -f -- "${created[i]}" ;;
          "$ssh_dir") rmdir -- "$ssh_dir" 2>/dev/null || true ;;
          "$release_dir") rm -rf -- "$release_dir" ;;
        esac
      done
      systemctl daemon-reload >/dev/null 2>&1 || true
    fi
  fi
  [[ -z $release_stage ]] || rm -rf -- "$release_stage"
  exit "$status"
}
trap on_exit EXIT
install -d -o root -g root -m 0755 "$release_root"
release_stage=$(mktemp -d "$release_root/.staging-XXXXXXXX")
git -C "$source_dir" archive --format=tar "$source_sha" ops | tar -xf - -C "$release_stage"
for f in "$release_stage"/ops/deploy/vps/bin/*; do chmod 0755 "$f"; done
find "$release_stage" -type d -exec chmod 0755 {} +
find "$release_stage" -type f ! -path '*/bin/*' -exec chmod 0644 {} +
ln -s ops/deploy/vps/bin "$release_stage/bin"
chown -R root:root "$release_stage"
python3 -m compileall -q "$release_stage/ops/deploy"
for f in "$release_stage"/ops/deploy/vps/bin/*; do [[ -x $f ]] || { printf 'non-executable ingress entrypoint: %s\n' "$f" >&2; exit 2; }; done
visudo -cf "$release_stage/ops/deploy/vps/sudoers/evcowbbe-trigger-ingest" >/dev/null
# In a crash between mutations, the key is not yet installed; inspect and
# recover using the explicit runbook rather than deleting unknown artifacts.
mv -T -- "$release_stage" "$release_dir"
release_stage=''
created+=("$release_dir")
ln -s "releases/$source_sha" "$install_root/current"
created+=("$install_root/current")
if [[ ! -e $state_dir ]]; then install -d -o evcow-orchestrator -g evcow-orchestrator -m 0700 "$state_dir"; fi
runuser -u evcow-orchestrator -- "$install_root/current/bin/evcowbbe-deploy-initialize"
[[ $(stat -c '%U:%G' "$state_dir/orchestrator.sqlite3") == evcow-orchestrator:evcow-orchestrator ]] || {
  printf '%s\n' 'SQLite database ownership failed verification' >&2; exit 2;
}
[[ $(stat -c '%a' "$state_dir/orchestrator.sqlite3") == 600 ]] || chmod 0600 "$state_dir/orchestrator.sqlite3"
install -o root -g root -m 0440 "$release_dir/ops/deploy/vps/sudoers/evcowbbe-trigger-ingest" "$sudoers_file"
created+=("$sudoers_file")
visudo -cf "$sudoers_file" >/dev/null
install -o root -g root -m 0644 "$release_dir/ops/deploy/vps/systemd/evcowbbe-deploy-reconcile.service" "$service_file"
created+=("$service_file")
install -o root -g root -m 0644 "$release_dir/ops/deploy/vps/systemd/evcowbbe-deploy-reconcile.timer" "$timer_file"
created+=("$timer_file")
systemctl daemon-reload
systemctl enable --now evcowbbe-deploy-reconcile.timer
# LAST AUTHORIZATION STEP: an immediately usable key is already restricted.
# No global SSH reload is necessary; no Match/Include ordering hazard exists.
install -d -o root -g root -m 0755 "$ssh_dir"
created+=("$ssh_dir")
authorized_line="restrict,command=\"/opt/evcowbbe-deploy/current/bin/evcowbbe-deploy-ssh-ingress\" $key_type $key_blob"
created+=("$authorized_keys")
(umask 077; printf '%s\n' "$authorized_line" > "$authorized_keys")
chown root:root "$authorized_keys"
chmod 0644 "$authorized_keys"
[[ $(stat -c '%U:%G' "$authorized_keys") == root:root ]] || exit 2
[[ $(stat -c '%a' "$authorized_keys") == 644 ]] || exit 2
sshd -t
installed=1
printf '%s\n' 'control-plane installed; application service and /srv/evcowbbe/current were not touched'
printf '%s\n' 'MANDATORY: verify real key-only forced SSH, shell/forwarding denial and timer recovery before declaring acceptance'
