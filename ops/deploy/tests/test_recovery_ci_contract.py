"""Prevent accidental promotion of disposable recovery tests into VPS-capable CI."""
from __future__ import annotations

import unittest
from pathlib import Path


class RecoveryCIContractTests(unittest.TestCase):
    def test_isolated_job_has_no_prod_credentials_or_ingress(self):
        workflow = Path('.github/workflows/ci.yml').read_text(encoding='utf-8')
        self.assertIn('name: Source CI', workflow)
        self.assertIn('  recovery-linux-integration:', workflow)
        self.assertIn('  verify:', workflow)
        isolated = workflow.split('  recovery-linux-integration:', 1)[1].split('  verify:', 1)[0]
        self.assertIn('runs-on: ubuntu-24.04', isolated)
        self.assertIn('POSTGRES_DB: evcowbbe_recovery_ci', isolated)
        self.assertIn('RECOVERY_TEST_ISOLATED:', isolated)
        self.assertIn('127.0.0.1:5432/evcowbbe_recovery_ci', isolated)
        self.assertIn('go test -count=1 -timeout 4m -tags=recovery_integration', isolated)
        self.assertIn('verify_recovery_systemd_streaming.py', isolated)
        self.assertNotIn('secrets.', isolated)
        self.assertNotIn('workflow_run:', isolated)
        self.assertNotIn('evcow-trigger@', isolated)
        self.assertNotIn('ssh ', isolated)
        self.assertNotIn('evcowbbe-deploy-recover-interrupted-build --', isolated)

    def test_isolated_fixture_fails_closed_before_any_database_access(self):
        text = Path('cmd/recovery-ledger-guard/recovery_integration_test.go').read_text(encoding='utf-8')
        self.assertIn('//go:build recovery_integration', text)
        for fragment in (
            'RECOVERY_TEST_ISOLATED', 'RECOVERY_TEST_POSTGRES_URL',
            'u.Host != "127.0.0.1:5432"',
            'u.Path != "/"+fixtureDatabase',
            'u.User.Username() != "recovery_ci"',
        ):
            self.assertIn(fragment, text)


if __name__ == '__main__':
    unittest.main()
