#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s --output <path> [--version <version>]\n' "$0" >&2
  exit 2
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output=""
version="0.0.0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output|-o)
      [[ $# -ge 2 ]] || usage
      output="$2"
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

[[ -n "$output" ]] || usage
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

build_time="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
output_dir="$(dirname "$output")"
mkdir -p "$output_dir"
temporary_output="$(mktemp "${output_dir}/.evcowbbe.XXXXXX")"
trap 'rm -f "$temporary_output"' EXIT

go build \
  -trimpath \
  -buildvcs=false \
  -ldflags="-X github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo.Version=${version} -X github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo.GitSHA=${git_sha} -X github.com/Transmogriffy-Global-Private-Limited/evcowbbe/internal/buildinfo.BuildTime=${build_time}" \
  -o "$temporary_output" \
  ./cmd/server

mv -f "$temporary_output" "$output"
trap - EXIT
printf 'release_build_output=%s\nrelease_git_sha=%s\nrelease_build_time=%s\n' "$output" "$git_sha" "$build_time"
