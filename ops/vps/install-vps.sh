#!/usr/bin/env bash
# One-time EVCOWBBE-only installer. Never clones, migrates, builds or edits Caddy.
set -euo pipefail

[[ "$(id -u)" == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $# -eq 0 || ( $# -eq 1 && $1 == --upgrade ) ]] || {
    echo 'Usage: install-vps.sh [--upgrade]' >&2; exit 2;
}
upgrade=0
[[ ${1:-} == --upgrade ]] && upgrade=1

base=/srv/evcowbbe
source_dir=$base/source
config_dir=/etc/evcowbbe
unit_src=$source_dir/ops/vps/evcowbbe.service
script_src=$source_dir/ops/vps/rehost-evcowbbe
unit_dst=/etc/systemd/system/evcowbbe.service
script_dst=/usr/local/sbin/rehost-evcowbbe

[[ -d $source_dir/.git && -f $unit_src && -f $script_src ]] || {
    echo 'STOP: EVCOWBBE repository/deployment files not found.' >&2; exit 1;
}
[[ -f $config_dir/host.env && -f $config_dir/secrets.env ]] || {
    echo 'STOP: Existing VPS-specific configuration is required.' >&2; exit 1;
}
[[ ! -L $base && ! -L $config_dir ]] || {
    echo 'STOP: Unexpected symlink in installation roots.' >&2; exit 1;
}
for target in "$base/releases" "$base/build-work" \
    /var/cache/evcowbbe-build /var/lib/evcowbbe \
    "$config_dir/host.env" "$config_dir/secrets.env" \
    "$config_dir/host.id" "$config_dir/managed.env"; do
    [[ ! -L $target ]] || { echo "STOP: Unexpected symlink: $target" >&2; exit 1; }
done

if ! getent passwd evcowbbe >/dev/null; then
    useradd --system --create-home --home-dir /var/lib/evcowbbe \
        --shell /usr/sbin/nologin --user-group evcowbbe
fi
if ! getent passwd evcowbbe-build >/dev/null; then
    useradd --system --create-home --home-dir /var/cache/evcowbbe-build \
        --shell /usr/sbin/nologin --user-group evcowbbe-build
fi
[[ "$(getent passwd evcowbbe | cut -d: -f6)" == /var/lib/evcowbbe ]] || {
    echo 'STOP: Unexpected evcowbbe account home.' >&2; exit 1;
}
[[ "$(getent passwd evcowbbe-build | cut -d: -f6)" == /var/cache/evcowbbe-build ]] || {
    echo 'STOP: Unexpected builder account home.' >&2; exit 1;
}

install -d -o root -g root -m 0755 "$base/releases"
install -d -o root -g root -m 0755 "$base/build-work"
install -d -o evcowbbe-build -g evcowbbe-build -m 0750 /var/cache/evcowbbe-build
install -d -o evcowbbe -g evcowbbe -m 0750 /var/lib/evcowbbe

chmod 0700 "$config_dir"
chmod 0600 "$config_dir/host.env" "$config_dir/secrets.env"

machine_id=$(tr -d '\n' </etc/machine-id)
[[ $machine_id =~ ^[[:xdigit:]]{32}$ ]] || {
    echo 'STOP: Invalid /etc/machine-id.' >&2; exit 1;
}
if [[ -e $config_dir/host.id ]]; then
    [[ $(<"$config_dir/host.id") == "$machine_id" ]] || {
        echo 'STOP: EVCOWBBE configuration belongs to another host.' >&2; exit 1;
    }
else
    ( umask 077; printf '%s\n' "$machine_id" >"$config_dir/host.id" )
fi
if [[ ! -e $config_dir/managed.env ]]; then
    ( umask 077
      printf 'EVCOWBBE_HOST_ID=%s\nEVCOWBBE_BUILD_REVISION=uninitialized\n' \
          "$machine_id" >"$config_dir/managed.env"
    )
fi
chmod 0600 "$config_dir/host.id" "$config_dir/managed.env"

for mapping in "$unit_src:$unit_dst" "$script_src:$script_dst"; do
    from=${mapping%%:*}
    to=${mapping#*:}
    if [[ -e $to || -L $to ]]; then
        [[ ! -L $to ]] || { echo "STOP: Unexpected symlink at $to" >&2; exit 1; }
        if ! cmp -s "$from" "$to" && (( upgrade == 0 )); then
            echo "STOP: $to exists and differs. Review; retry with --upgrade." >&2
            exit 1
        fi
    fi
done

install -o root -g root -m 0644 "$unit_src" "$unit_dst"
install -o root -g root -m 0755 "$script_src" "$script_dst"
systemctl daemon-reload

echo 'EVCOWBBE_VPS_INSTALL=PASS'
echo 'The service is installed but not started. Run rehost-evcowbbe to deploy.'
echo 'Caddy is intentionally unchanged; see docs/VPS2_MANUAL_DEPLOYMENT.md.'
