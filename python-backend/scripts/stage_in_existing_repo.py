#!/usr/bin/env python3
"""Stage Python CMS in EXISTING repo without disturbing Go code or hidden config.

Run from the unpacked candidate source (not in the existing repository).
Only writes `python-backend/` on an explicitly selected non-main Git branch.
Does not commit, push, deploy, change existing Go files or run migrations.
"""
import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'__pycache__', '.pytest_cache', '.ruff_cache', '.venv', '.git', 'dist', 'build'}


def cmd(repo: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('existing_repo', type=Path)
    parser.add_argument('--expected-base', default='070cd06514b4d63b29aaa12ff115d08c9551d0c8')
    args = parser.parse_args()
    dest_repo = args.existing_repo.resolve()
    if not (dest_repo / '.git').exists():
        raise SystemExit('STOP: destination is not a Git checkout')
    if cmd(dest_repo, 'status', '--porcelain', '--untracked-files=all'):
        raise SystemExit('STOP: checkout must be clean; preserve all existing changes')
    branch = cmd(dest_repo, 'branch', '--show-current')
    if not branch or branch in ('main', 'master', 'production'):
        raise SystemExit('STOP: create and switch to a dedicated review branch first')
    head = cmd(dest_repo, 'rev-parse', 'HEAD')
    if head != args.expected_base:
        raise SystemExit(f'STOP: checkout revision {head} differs from pinned source {args.expected_base}; reconcile manually')
    target = dest_repo / 'python-backend'
    if target.exists() or target.is_symlink():
        raise SystemExit('STOP: python-backend already exists; no overwrites permitted')
    if ROOT == dest_repo or dest_repo in ROOT.parents:
        raise SystemExit('STOP: source and repository location overlap')
    def ignored(directory: str, names: list[str]):
        return [n for n in names if n in SKIP or n == '.env' or n.startswith('.env.') or n.endswith('.pyc')]
    shutil.copytree(ROOT, target, ignore=ignored, symlinks=False)
    print(f'STAGED={target}')
    print('Unmodified: Go backend, .git, .env, Caddy, systemd, PostgreSQL, VPS2')
    print('NEXT: inspect git status/diff, run local tests and real PostgreSQL suite')
    print('Do not merge into main or deploy until complete FE compatibility gates pass')


if __name__ == '__main__':
    main()
