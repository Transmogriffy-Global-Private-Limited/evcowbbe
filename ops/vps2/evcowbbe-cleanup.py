#!/usr/bin/env python3
"""Explicit-only EVCOWBBE scratch database and /root test artifact cleanup.

Commands `plan` (read-only) and `apply` (protected backups before DB drops).
Never changes cluster configuration, shared roles, live Go/Django DBs, or other projects.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

DBS = (
 'cow_acceptance_20261029_test','cow_django_stage_20261102',
 *('evcowbbe_atlas_api_wp3_20261029_%02d_test'%i for i in range(1,9)),
 *('evcowbbe_atlas_api_wp3b_20261029_%02d_test'%i for i in range(1,9)),
 'evcowbbe_atlas_dev_20261029_test','evcowbbe_atlas_dev_wp3b_20261029_test',
 'evcowbbe_atlas_fresh_20261029_test','evcowbbe_atlas_fresh_wp3b_20261029_test',
 'evcowbbe_atlas_populated_20261029_test','evcowbbe_atlas_populated_wp3b_20261029_test',
 'evcowbbe_f3_20261028083109_8c9982db_test',
 'evcowbbe_restore_20261027092427_25093','evcowbbe_restore_f3_20261028082255_31c7b2e2',
 'evcowbbe_restore_fleet3_20261028051446_6051_6371',
)
assert len(DBS)==28 and len(set(DBS))==28
# Reusable project-owned acceptance DB remains required by scripts/run_pg_tests.py.
PROTECTED_TEST_DB='cow_wp31a_20261030_test'
assert set(DBS).isdisjoint({'evcowbbe','evcowbbe_django_next',PROTECTED_TEST_DB})
ROOT=Path('/root')
TEST_DIRS=('evcowbbe-pg-acceptance-20261029',) # Preserve wp31a reusable test environment until harness migration
ARCHIVE_NAMES=(
 'evcowbbe-admin-auth-scratch-acceptance.sh', 'evcowbbe-build-django-release.sh',
 'evcowbbe-cutover-controller-v2-bundle.zip', 'evcowbbe-cutover-controller-v3-bundle.zip',
 'evcowbbe-cutover-controller-v4-bundle.zip', 'evcowbbe-cutover-controller-v5-bundle.zip',
 'evcowbbe-cutover-controller.py','evcowbbe-cutover-topology-audit.sh',
 'evcowbbe-cutover-v2','evcowbbe-cutover-v3','evcowbbe-cutover-v4','evcowbbe-cutover-v5',
 'evcowbbe-dev-cutover','evcowbbe-dev-single-backend-switch-20261102.zip',
 'evcowbbe-django-outbox-delivery-rehearsal.sh',
 'evcowbbe-go-rollback-rehearsal-v2.sh','evcowbbe-go-rollback-rehearsal-v3.sh',
 'evcowbbe-go-rollback-rehearsal.sh','evcowbbe-mail-worker-v1-review',
 'evcowbbe-mail-worker-v1-review.zip','evcowbbe-paired-db-switch-rehearsal.sh',
 'evcowbbe-reconcile-inventory-20261102.txt','evcowbbe-reconcile-inventory.sh',
 'evcowbbe-resume-django-release.sh','evcowbbe-v5-approved-admin-scratch-gate.py',
 'evcowbbe_dev_interrupted_resume_once.py',
 'evcowbbe_vps2_pre_cutover_readonly_20261031.sh',
 'evcowbbe_wp3_api_isolation_and_diff_audit_20261029.sh',
 'evcowbbe_wp3_fbd21fd.bundle',
 'evcowbbe_wp3_fbd21fd_final_isolated_rehearsal_20261029.sh',
 'evcowbbe_wp3_fbd21fd_resume_after_expected_ledger_check_20261029.sh',
 'evcowbbe_wp3_ledger_reconciliation_resume_20261029.sh',
 'evcowbbe_wp3_postgres_rehearsal_20261029.sh',
 'evcowbbe_wp3_resume_revisions_20261029.sh',
 'evcowbbe_wp3_resume_revisions_20261029_v2.sh',
)
BASE=Path('/var/backups/evcowbbe')
STATE=Path('/var/lib/evcowbbe-rehost/project-cleanup.json')
LOCK=Path('/run/lock/evcowbbe-rehost.lock')

class Blocked(Exception):pass

def need(ok,msg):
    if not ok:raise Blocked(msg)

def call(args,*,out=None,inp=None,timeout=180,check=True):
    p=subprocess.run(args,stdout=out if out is not None else subprocess.PIPE,
                     stdin=inp or subprocess.DEVNULL,stderr=subprocess.PIPE,
                     text=out is None,timeout=timeout,check=False)
    if check and p.returncode:raise Blocked('PROCESS_FAILED='+args[0]+' rc='+str(p.returncode))
    return p

def psql(query):
    return call(['runuser','-u','postgres','--','psql','-X','-A','-F','|','-t',
                 '-v','ON_ERROR_STOP=1','-d','postgres','-c',query],timeout=20).stdout.strip()

def row(name):
    need(name in DBS,'DB_NOT_ALLOWLISTED')
    name_sql="'"+name+"'"  # allowlist fixed ASCII identifiers, no user-supplied SQL
    q=("SELECT datname,pg_get_userbyid(datdba),pg_database_size(oid),"
       "(SELECT count(*) FROM pg_stat_activity WHERE datname="+name_sql+") "
       "FROM pg_database WHERE datname="+name_sql)
    s=psql(q)
    if not s:return None
    parts=s.split('|')
    need(len(parts)==4 and parts[0]==name,'DB_CATALOG_UNEXPECTED='+name)
    return {'name':name,'owner':parts[1],'bytes':int(parts[2]),'sessions':int(parts[3])}

def checksum(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda:f.read(1048576),b''):h.update(part)
    return h.hexdigest()

def saved(data):
    need(STATE.parent.is_dir() and STATE.parent.stat().st_uid==0 and STATE.parent.stat().st_mode&0o077==0,'STATE_PARENT_NOT_ROOT_PRIVATE')
    fd=os.open(STATE.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:
        tmp=STATE.parent/('.cow-cleanup-'+str(os.getpid()))
        with tmp.open('x') as f:
            json.dump(data,f,indent=2,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
        tmp.chmod(0o600);os.replace(tmp,STATE);os.fsync(fd)
    finally:os.close(fd)

def load():
    if not STATE.exists():return None
    need(STATE.is_file() and not STATE.is_symlink(),'CLEANUP_JOURNAL_NOT_REGULAR_FILE')
    need(STATE.stat().st_uid==0 and STATE.stat().st_mode & 0o077==0,'CLEANUP_JOURNAL_PERMISSIONS')
    j=json.loads(STATE.read_text())
    need(j.get('version')==1 and j.get('databases') is not None,'CLEANUP_JOURNAL_INVALID')
    return j

def boundary():
    need(os.geteuid()==0,'ROOT_REQUIRED')
    marker=Path('/var/lib/evcowbbe-rehost/runtime.json')
    need(marker.is_file() and not marker.is_symlink(),'CANONICAL_RUNTIME_NOT_ADOPTED')
    state=json.loads(marker.read_text())
    need(state.get('backend')=='django' and state.get('database')=='evcowbbe_django_next' and state.get('phase')=='ACTIVE', 'DJANGO_NOT_COMMITTED')
    need(call(['systemctl','is-active','--quiet','evcowbbe.service'],check=False).returncode==0,'SERVICE_NOT_ACTIVE')
    need(call(['systemctl','is-active','--quiet','evcowbbe-django-stage.service'],check=False).returncode!=0,'STAGING_STILL_ACTIVE')
    call(['curl','--noproxy','*','--fail','--silent','--show-error','--max-time','8','http://127.0.0.1:18280/health/ready'])
    need(psql("SELECT count(*) FROM pg_database WHERE datname IN ('evcowbbe','evcowbbe_django_next') AND pg_get_userbyid(datdba)='evcowbbe_app'")=='2','CANONICAL_DATABASES_CHANGED')
    need(psql("SELECT count(*) FROM pg_database WHERE datname='cow_wp31a_20261030_test'")=='1','REUSABLE_TEST_HARNESS_DB_MISSING')
    need(not Path('/srv/evcowbbe/source/.git').is_symlink(),'UNEXPECTED_GIT_PATH')

def active_stage():
    return call(['systemctl','is-active','--quiet','evcowbbe-django-stage.service'],check=False).returncode==0

def refs_to_test_dir(p):
    # Consider current process roots/cwds and all runtime unit definitions.
    units=['evcowbbe.service','evcowbbe-mail.service','evcowbbe-mail.timer']
    for unit in units:
        text=call(['systemctl','cat',unit]).stdout
        need(str(p) not in text,'UNIT_REFERENCES_TEST_DIR='+str(p))
    # Catch scheduled/supervised references to a legacy file outside the three
    # canonical units, without printing private unit or cron content.
    for directory in (Path('/etc/systemd/system'),Path('/etc/cron.d')):
        if directory.exists():
            for file in directory.rglob('*'):
                if not file.is_file() or file.is_symlink() or file.stat().st_size>1048576:
                    continue
                if str(p).encode() in file.read_bytes():
                    raise Blocked('SCHEDULED_REFERENCE_TO_LEGACY_PATH='+str(p))
    cron=Path('/etc/crontab')
    if cron.is_file() and str(p).encode() in cron.read_bytes():
        raise Blocked('CRONTAB_REFERENCES_LEGACY_PATH='+str(p))
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():continue
        try:
            command=(item/'cmdline').read_bytes()
            need(str(p).encode() not in command,'RUNNING_PROCESS_COMMAND_USES_TEST_DIR='+str(p))
        except (OSError,PermissionError):pass
        for suffix in ('cwd','exe'):
            try:target=(item/suffix).resolve(strict=True)
            except (OSError,RuntimeError):continue
            need(not target.is_relative_to(p),'RUNNING_PROCESS_USES_TEST_DIR='+str(p))

def plan():
    boundary()
    j=load()
    print('PROTECTED_REUSABLE_TEST_DB='+PROTECTED_TEST_DB)
    for name in DBS:
        x=row(name)
        if x:
            print('SCRATCH_DB='+x['name']+' OWNER='+x['owner']+' BYTES='+str(x['bytes'])+' SESSIONS='+str(x['sessions']))
        else:print('SCRATCH_DB_ALREADY_ABSENT='+name)
    for name in TEST_DIRS:
        p=ROOT/name
        if p.exists() and p.is_dir() and not p.is_symlink():
            print('REMOVE_AFTER_DEPENDENCY_CHECK='+str(p))
    stage=Path('/srv/evcowbbe-python-stage')
    if stage.exists() or stage.is_symlink():
        print('STAGE_TREE_ARCHIVE_CANDIDATE='+str(stage))
    print('STAGE_UNIT_FRAGMENT='+call(['systemctl','show','-P','FragmentPath','evcowbbe-django-stage.service']).stdout.strip())
    for name in ARCHIVE_NAMES:
        p=ROOT/name
        if p.exists() or p.is_symlink():print('ARCHIVE='+str(p))
    print('JOURNAL='+('NONE' if not j else j.get('phase','UNKNOWN')))
    print('PLAN_ONLY=YES')

def perform():
    boundary()
    j=load()
    if j is None:
        folder=BASE/('project-cleanup-'+str(int(time.time())))
        need(not folder.exists(),'CLEANUP_BACKUP_COLLISION')
        folder.mkdir(mode=0o700)
        (folder/'db').mkdir(mode=0o700)
        (folder/'legacy').mkdir(mode=0o700)
        j={'version':1,'phase':'IN_PROGRESS','backup':str(folder),'databases':{},'files':{},'test_dirs':{}}
        # Baseline already-absent scratch DBs safely; absence after a crash
        # must still be backed by a recorded archive before being reconciled.
        for name in DBS:
            if row(name) is None:j['databases'][name]={'preexisting_absent':True}
        saved(j)
    else:
        folder=Path(j['backup'])
        need(folder.parent==BASE and folder.name.startswith('project-cleanup-') and folder.is_dir() and not folder.is_symlink(),'INVALID_RESUME_BACKUP_PATH')
    print('PROJECT_CLEANUP_BACKUP='+str(folder),flush=True)
    # Preflight all remaining scratch DB identities before first destructive action.
    for name in DBS:
        x=row(name)
        if not x:
            rec=j['databases'].get(name)
            need(rec is not None,'SCRATCH_DB_ABSENT_WITHOUT_LEDGER='+name)
            if rec.get('preexisting_absent'):
                continue
            need(rec.get('sha256'),'SCRATCH_DB_BACKUP_LEDGER_MISSING='+name)
            file=folder/'db'/(name+'.dump')
            need(file.is_file() and not file.is_symlink() and checksum(file)==rec['sha256'],
                 'ABSENT_DB_BACKUP_MISSING='+name)
            if not rec.get('dropped'):
                rec['dropped']=True;saved(j)
                print('RESUMED_PREVIOUS_DB_DROP='+name,flush=True)
            continue
        need(x['owner'] in ('evcowbbe_app','postgres'),'UNRECOGNIZED_SCRATCH_DB_OWNER='+name+' '+x['owner'])
        need(x['sessions']==0,'ACTIVE_DATABASE_SESSIONS='+name)
    for name in DBS:
        boundary()
        x=row(name)
        if not x:continue
        dest=folder/'db'/(name+'.dump')
        rec=j['databases'].get(name)
        if rec:
            need(rec.get('sha256') and dest.is_file() and not dest.is_symlink() and checksum(dest)==rec['sha256'], 'ARCHIVED_DB_HASH_DRIFT='+name)
        else:
            need(not dest.exists() and not dest.is_symlink(),'DB_ARCHIVE_CONFLICT='+name)
            fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as file:
                call(['runuser','-u','postgres','--','pg_dump','--format=custom','--no-owner','--no-privileges','--dbname',name],
                     out=file,timeout=240)
                file.flush();os.fsync(file.fileno())
            need(dest.stat().st_size>1024,'DB_ARCHIVE_TOO_SMALL='+name)
            with dest.open('rb') as source:
                call(['runuser','-u','postgres','--','pg_restore','--list'],inp=source,out=subprocess.DEVNULL,timeout=45)
            rec={'sha256':checksum(dest),'bytes':dest.stat().st_size,'owner':x['owner'],'dropped':False}
            j['databases'][name]=rec;saved(j)
            print('DB_BACKUP_VERIFIED='+name,flush=True)
        # Reconfirm that the target has not changed or attracted clients while dumping.
        y=row(name)
        need(y and y['owner']==x['owner'] and y['sessions']==0,'DB_CHANGED_DURING_BACKUP='+name)
        need(checksum(dest)==rec['sha256'],'DB_BACKUP_CHECKSUM_CHANGED='+name)
        call(['runuser','-u','postgres','--','psql','-X','-v','ON_ERROR_STOP=1','-d','postgres',
              '-c','DROP DATABASE "'+name+'"'],timeout=75)
        need(row(name) is None,'DB_DROP_NOT_OBSERVED='+name)
        rec['dropped']=True;saved(j)
        print('SCRATCH_DB_REMOVED='+name,flush=True)
    # Remove only two exact, inactive test-only environments. They contain
    # reproducible test venvs, not the sealed live release.
    for name in TEST_DIRS:
        p=ROOT/name
        if not p.exists():continue
        need(p.is_dir() and not p.is_symlink() and p.stat().st_uid==0,'TEST_DIR_PATH_UNSAFE='+name)
        refs_to_test_dir(p)
        j['test_dirs'][name]={'removed':False,'bytes_estimate':sum(x.stat().st_size for x in p.rglob('*') if x.is_file() and not x.is_symlink())}
        saved(j)
        shutil.rmtree(p)
        need(not p.exists(),'TEST_DIR_NOT_REMOVED='+name)
        j['test_dirs'][name]['removed']=True;saved(j)
        print('TEST_ENV_REMOVED='+name,flush=True)
    # Move small historical artifacts to protected archive, rather than delete.
    # No hidden wildcard matching; only exact known files/directories.
    for name in ARCHIVE_NAMES:
        p=ROOT/name
        if not p.exists() and not p.is_symlink():
            archived=folder/'legacy'/name
            if archived.exists() and name not in j['files']:
                j['files'][name]={'archived':True,'recovered_after_interruption':True};saved(j)
            continue
        need(not p.is_symlink() and p.stat().st_uid==0,'LEGACY_PATH_UNSAFE='+name)
        # Skip the actual running commands by inspecting systemd and process cwd/exe.
        refs_to_test_dir(p)
        dest=folder/'legacy'/name
        need(not dest.exists() and not dest.is_symlink(),'LEGACY_ARCHIVE_CONFLICT='+name)
        os.rename(p,dest)  # same filesystem; fail instead of non-atomic copy
        j['files'][name]={'archived':True};saved(j)
        print('LEGACY_ARTIFACT_ARCHIVED='+name,flush=True)
    # Retire the disabled stage unit only after retiring its isolated DB.
    stage_unit=Path('/etc/systemd/system/evcowbbe-django-stage.service')
    if stage_unit.exists() or stage_unit.is_symlink():
        need(stage_unit.is_file() and not stage_unit.is_symlink() and stage_unit.stat().st_uid==0,
             'STAGE_UNIT_PATH_UNSAFE')
        need(call(['systemctl','is-enabled','evcowbbe-django-stage.service'],check=False).stdout.strip()=='disabled',
             'STAGE_UNIT_NOT_DISABLED')
        need(not active_stage(),'STAGE_UNIT_ACTIVE')
        load_path=call(['systemctl','show','-P','FragmentPath','evcowbbe-django-stage.service']).stdout.strip()
        need(load_path==str(stage_unit),'STAGE_UNIT_FRAGMENT_CHANGED')
        dropins=call(['systemctl','show','-P','DropInPaths','evcowbbe-django-stage.service']).stdout.strip()
        need(not dropins,'STAGE_UNIT_HAS_DROPINS')
        dest=folder/'legacy'/'evcowbbe-django-stage.service'
        need(not dest.exists() and not dest.is_symlink(),'STAGE_UNIT_BACKUP_CONFLICT')
        dest.write_bytes(stage_unit.read_bytes());dest.chmod(0o600)
        need(checksum(dest)==checksum(stage_unit),'STAGE_UNIT_BACKUP_FAILED')
        stage_unit.unlink()
        call(['systemctl','daemon-reload'],timeout=40)
        need(not active_stage(),'STAGE_UNIT_REACTIVATED')
        j['files']['evcowbbe-django-stage.service']={'archived':True};saved(j)
        print('OBSOLETE_STAGE_UNIT_RETIRED=PASS',flush=True)
    stage=Path('/srv/evcowbbe-python-stage')
    if stage.exists() or stage.is_symlink():
        need(stage.is_dir() and not stage.is_symlink() and stage.stat().st_uid==0,
             'STAGE_TREE_PATH_UNSAFE')
        need(not active_stage(),'STAGE_BACKEND_ACTIVE')
        refs_to_test_dir(stage)
        # Historical review worktree must be clean and its content present in Git.
        src=stage/'source'
        need((src/'.git').exists(),'STAGE_GIT_REVIEW_MISSING')
        need(call(['git','-C',str(src),'status','--porcelain','--untracked-files=all']).stdout.strip()=='',
             'STAGE_REVIEW_TREE_HAS_UNCOMMITTED_WORK')
        stage_rev=call(['git','-C',str(src),'rev-parse','HEAD']).stdout.strip()
        need(re.fullmatch('[0-9a-f]{40}',stage_rev) is not None,'STAGE_GIT_REV_INVALID')
        # Moving the entire staging tree is reversible; no file deletion.
        dest=folder/'legacy'/'evcowbbe-python-stage'
        need(not dest.exists() and not dest.is_symlink(),'STAGE_ARCHIVE_CONFLICT')
        os.rename(stage,dest)
        j['files']['evcowbbe-python-stage']={'archived':True,'git_revision':stage_rev};saved(j)
        print('DISABLED_STAGE_TREE_ARCHIVED=PASS',flush=True)
    boundary()
    j['phase']='COMPLETE';saved(j)
    print('EVCOWBBE_PROJECT_CLEANUP=PASS')
    print('CANONICAL_DB_PRESERVED=evcowbbe_django_next')
    print('TEST_HARNESS_DB_PRESERVED='+PROTECTED_TEST_DB)
    print('GO_ROLLBACK_DB_PRESERVED=evcowbbe')
    print('SHARED_POSTGRESQL_ROLES_AND_SERVICES_UNCHANGED=YES')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','apply'))
    args=parser.parse_args()
    try:
        need(os.geteuid()==0,'ROOT_REQUIRED')
        if args.action=='plan':plan()
        else:
            fd=os.open(LOCK,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            os.fchmod(fd,0o600)
            with os.fdopen(fd,'a+b') as file:
                fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
                perform()
    except (Blocked,OSError,ValueError,subprocess.TimeoutExpired) as e:
        print('PROJECT_CLEANUP_BLOCKED='+str(e),file=sys.stderr)
        raise SystemExit(1)
