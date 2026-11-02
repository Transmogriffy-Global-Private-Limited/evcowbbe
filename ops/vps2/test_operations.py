"""Offline invariants for the tracked VPS2 operator. No root or VPS needed."""
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent

def load(name,file):
    loader=SourceFileLoader(name,str(ROOT/file))
    spec=importlib.util.spec_from_loader(name,loader)
    module=importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

class Operations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.op=load('cow_rehost_under_test','rehost-evcowbbe')
        cls.cleanup=load('cow_cleanup_under_test','evcowbbe-cleanup.py')

    def test_single_api_and_db_boundary(self):
        launcher=(ROOT/'launcher.py').read_text()
        self.assertIn('127.0.0.1:18280',launcher)
        self.assertIn("'/evcowbbe_django_next'",launcher)
        self.assertIn("'/evcowbbe'",launcher)
        self.assertIn("'COW_HAL_BASE_URL'",launcher)
        self.assertNotIn('0.0.0.0:18280',launcher)

    def test_cleanup_database_allowlist_is_explicit_and_disjoint(self):
        names=set(self.cleanup.DBS)
        self.assertEqual(28,len(names))
        self.assertTrue(all(n.startswith('evcowbbe_') or n.startswith('cow_') for n in names))
        self.assertFalse({'evcowbbe','evcowbbe_django_next','cow_wp31a_20261030_test'}&names)

    def test_unlisted_database_never_queried(self):
        with patch.object(self.cleanup,'psql',side_effect=AssertionError('unexpected DB query')):
            with self.assertRaises(self.cleanup.Blocked):
                self.cleanup.row('shuttlebe')

    def test_clean_server_paths_only(self):
        self.assertEqual(str(self.op.SOURCE),'/srv/evcowbbe/source')
        self.assertEqual(str(self.op.CURRENT),'/srv/evcowbbe/current-python')
        self.assertEqual(str(self.op.STATE),'/var/lib/evcowbbe-rehost/runtime.json')
        self.assertEqual(str(self.cleanup.STATE),'/var/lib/evcowbbe-rehost/project-cleanup.json')
        self.assertEqual(str(self.op.INSTALL['rehost-evcowbbe']),'/usr/local/sbin/rehost-evcowbbe')

    def test_schema_change_blocks_before_promotion(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            for rev in ('a'*40,'b'*40):
                (p/rev/'source/cow/migrations').mkdir(parents=True)
            (p/('a'*40)/'source/cow/migrations/0001_initial.py').write_text('old')
            (p/('b'*40)/'source/cow/migrations/0001_initial.py').write_text('changed')
            with patch.object(self.op,'RELEASES',p):
                with self.assertRaises(self.op.Stop):
                    self.op.schema_identical('a'*40,'b'*40)

    def test_all_runtime_artefacts_tracked(self):
        names=set(self.op.INSTALL)
        self.assertEqual(7,len(names))
        self.assertTrue(all((ROOT/n).is_file() for n in names))

    def test_recovery_ledger_paths_off_public_webroot(self):
        self.assertTrue(str(self.cleanup.BASE).startswith('/var/backups/evcowbbe'))
        self.assertTrue(str(self.op.BACKUPS).startswith('/var/backups/evcowbbe'))
        self.assertTrue(str(self.op.LOCK).startswith('/run/lock'))

    def test_mail_requires_root_approved_marker(self):
        guard=(ROOT/'mail_guard.py').read_text()
        self.assertIn("j.get('phase') != 'ACTIVE'",guard)
        self.assertIn("st.st_uid != 0",guard)
        self.assertIn('evcowbbe.service',guard)

if __name__=='__main__':unittest.main()
