#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s --server-output <path> --migrator-output <path> [--version <version>]\n' "$0" >&2
  exit 2
}

resolve_output_path() {
  local requested_path="$1"
  local output_dir
  local output_name

  output_dir="$(dirname -- "$requested_path")"
  output_name="$(basename -- "$requested_path")"
  [[ "$output_name" != "." && "$output_name" != ".." ]] || {
    printf 'release output must name a file: %s\n' "$requested_path" >&2
    exit 2
  }

  mkdir -p -- "$output_dir"
  output_dir="$(cd -- "$output_dir" && pwd -P)"
  printf '%s/%s\n' "$output_dir" "$output_name"
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
server_output=""
migrator_output=""
version="0.0.0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-output)
      [[ $# -ge 2 ]] || usage
      server_output="$2"
      shift 2
      ;;
    --migrator-output)
      [[ $# -ge 2 ]] || usage
      migrator_output="$2"
      shift 2
      ;;
    --version)
      [[ $# -ge 2 ]] || usage
      version="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

[[ -n "$server_output" && -n "$migrator_output" ]] || usage
[[ "$version" =~ ^[A-Za-z0-9._+-]+$ ]] || {
  printf 'version must contain only ASCII letters, digits, dot, underscore, plus, or hyphen\n' >&2
  exit 2
}

cd "$repo_root"

git_sha="$(git rev-parse --verify -q HEAD)" || {
  printf 'release build requires a repository with a committed HEAD\n' >&2
  exit 1
}
[[ "$git_sha" =~ ^[0-9a-f]{40}$ ]] || {
  printf 'release build resolved an invalid Git SHA: %s\n' "$git_sha" >&2
  exit 1
}

if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  printf 'release build requires a clean working tree, including no untracked files\n' >&2
  exit 1
fi

server_output="$(resolve_output_path "$server_output")"
migrator_output="$(resolve_output_path "$migrator_output")"
[[ "$server_output" != "$migrator_output" ]] || {
  printf 'server and migrator outputs must be different paths\n' >&2
  exit 2
}
[[ ! -d "$server_output" && ! -d "$migrator_output" ]] || {
  printf 'release outputs must not be directories\n' >&2
  exit 2
}

build_time="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ldflags="-X github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo.Version=${version} -X github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo.GitSHA=${git_sha} -X github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo.BuildTime=${build_time}"
server_output_dir="$(dirname -- "$server_output")"
migrator_output_dir="$(dirname -- "$migrator_output")"
server_output_name="$(basename -- "$server_output")"
migrator_output_name="$(basename -- "$migrator_output")"
server_temporary="$(mktemp "${server_output_dir}/.${server_output_name}.tmp.XXXXXX")"
migrator_temporary="$(mktemp "${migrator_output_dir}/.${migrator_output_name}.tmp.XXXXXX")"
server_backup=""
migrator_backup=""
server_published=0
migrator_published=0

cleanup() {
  local status=$?
  trap - EXIT

  if (( status != 0 )); then
    if (( migrator_published )); then
      rm -f -- "$migrator_output" || true
    fi
    if [[ -n "$migrator_backup" ]]; then
      mv -f -- "$migrator_backup" "$migrator_output" || true
    fi
    if (( server_published )); then
      rm -f -- "$server_output" || true
    fi
    if [[ -n "$server_backup" ]]; then
      mv -f -- "$server_backup" "$server_output" || true
    fi
  fi

  rm -f -- "$server_temporary" "$migrator_temporary"
  [[ -z "$server_backup" ]] || rm -f -- "$server_backup"
  [[ -z "$migrator_backup" ]] || rm -f -- "$migrator_backup"
  exit "$status"
}
trap cleanup EXIT

go build \
  -trimpath \
  -buildvcs=false \
  -ldflags="$ldflags" \
  -o "$server_temporary" \
  ./cmd/server

go build \
  -trimpath \
  -buildvcs=false \
  -ldflags="$ldflags" \
  -o "$migrator_temporary" \
  ./cmd/migrate

if [[ -e "$server_output" || -L "$server_output" ]]; then
  server_backup="$(mktemp "${server_output_dir}/.${server_output_name}.backup.XXXXXX")"
  rm -f -- "$server_backup"
  mv -f -- "$server_output" "$server_backup"
fi
if [[ -e "$migrator_output" || -L "$migrator_output" ]]; then
  migrator_backup="$(mktemp "${migrator_output_dir}/.${migrator_output_name}.backup.XXXXXX")"
  rm -f -- "$migrator_backup"
  mv -f -- "$migrator_output" "$migrator_backup"
fi

mv -f -- "$server_temporary" "$server_output"
server_temporary=""
server_published=1
mv -f -- "$migrator_temporary" "$migrator_output"
migrator_temporary=""
migrator_published=1

rm -f -- "$server_backup" "$migrator_backup"
server_backup=""
migrator_backup=""
trap - EXIT

printf 'release_server_output=%s\n' "$server_output"
printf 'release_migrator_output=%s\n' "$migrator_output"
printf 'release_git_sha=%s\n' "$git_sha"
printf 'release_build_time=%s\n' "$build_time"
printf 'release_version=%s\n' "$version"
