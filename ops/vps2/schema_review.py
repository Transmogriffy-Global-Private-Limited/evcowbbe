#!/usr/bin/env python3
"""Read-only VPS2 schema-change evidence inventory. NEVER migrates or deploys.

The running rehost operator is deliberately code-only. Do not bypass its
schema-change check. A production migration execution/recovery operator needs
separate implementation, rehearsal, approval and change-window controls.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''):h.update(chunk)
    return h.hexdigest()


def inventory(folder:Path)->dict[str,str]:
    if not folder.is_dir() or folder.is_symlink():raise ValueError('Source directory missing or symlinked')
    mig=folder/'cow'/'migrations'
    if not mig.is_dir() or mig.is_symlink():raise ValueError('Migration history missing')
    files=sorted(mig.glob('0*.py'))
    if not files:raise ValueError('Migration history is empty')
    for p in files:
        if not p.is_file() or p.is_symlink():raise ValueError('Migration file type not allowed')
    return {p.name:sha(p) for p in files}


def plan(before:Path,after:Path)->dict:
    b=inventory(before); a=inventory(after)
    if not (after/'cow/models.py').is_file() or (after/'cow/models_zone.py').exists():
        raise ValueError('Target lacks one canonical model declaration source')
    changed=[n for n in sorted(b) if a.get(n)!=b[n]]
    removed=sorted(set(b)-set(a))
    added=sorted(set(a)-set(b))
    if changed or removed:raise ValueError('Historical migration rewrite detected; approval refused')
    if added and min(added) <= max(b):raise ValueError('Migration lineage does not append cleanly')
    return {
        'operator':'read-only-schema-review-v1',
        'source_migrations_prior':len(b),
        'source_migrations_target':len(a),
        'appended_migration_names':added,
        'migration_change_required':bool(added),
        'production_migration_executed':False,
        'runtime_deployment_executed':False,
        'production_apply_implemented':False,
        'release_rehost_gate':'UNCHANGED_AND_ENFORCED',
        'required_before_live_change':[
            'operator-approved change window and release compatibility plan',
            'actual authoritative database identity and schema ledger read-back',
            'verified custom-format backup and independent restore test',
            'pretested forward/reverse/data-migration behavior on restored clone',
            'active traffic and job/outbox quiescence strategy with lock timeout',
            'DB backup retention, recovery plan and rollback decision boundaries',
            'approved database migration executor and post-change validation',
        ] if added else [],
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prior-source',type=Path,required=True)
    p.add_argument('--candidate-source',type=Path,required=True)
    p.add_argument('--backup',type=Path)
    p.add_argument('--backup-sha256')
    args=p.parse_args()
    if args.prior_source.is_symlink() or args.candidate_source.is_symlink():
        raise ValueError('Source directory symlinks are not allowed')
    result=plan(args.prior_source.resolve(),args.candidate_source.resolve())
    if args.backup:
        if not args.backup_sha256 or len(args.backup_sha256)!=64:
            raise ValueError('Backup SHA256 must be supplied for independent verification')
        if args.backup.is_symlink(): raise ValueError('Backup symlink not allowed')
        backup=args.backup.resolve()
        if not backup.is_file() or sha(backup)!=args.backup_sha256.lower():
            raise ValueError('Backup missing or SHA256 mismatch')
        cmd=subprocess.run(['pg_restore','--list',str(backup)],capture_output=True,text=True)
        if cmd.returncode or 'TABLE' not in cmd.stdout:
            raise ValueError('Custom-format backup pg_restore listing failed')
        result['backup_sha256_verified']=True
        result['backup_archive_listed']=True
        result['backup_restored_and_reconciled']=False
    print(json.dumps(result,sort_keys=True,indent=2))
    print('SCHEMA_REVIEW_READ_ONLY=PASS')
    if result['migration_change_required']:
        print('VPS2_SCHEMA_DEPLOYMENT_GATE=BLOCKED_PENDING_SEPARATE_OPERATOR')

if __name__=='__main__':
    try:main()
    except (OSError,ValueError) as exc:
        print('SCHEMA_REVIEW_REFUSED: '+str(exc),file=sys.stderr)
        raise SystemExit(2)
