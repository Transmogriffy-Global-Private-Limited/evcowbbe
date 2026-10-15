"""Linux-only real pipe framing tests: never connect to PostgreSQL or VPS2."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest

from ops.deploy.vps.interrupted_build_pg_fence import Guard, PgFenceBlocked
from ops.deploy.vps.interrupted_build_ledger import EMPTY_LEDGER_SHA256


@unittest.skipUnless(os.name == 'posix', 'Linux/POSIX pipe semantics required')
class GuardStreamingTests(unittest.TestCase):
    def launch(self, code: str):
        proc = subprocess.Popen(
            [sys.executable, '-B', '-u', '-c', code],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: self.stop(proc))
        return Guard(proc)

    @staticmethod
    def stop(proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=4)

    def test_no_newline_cannot_hang_past_deadline(self):
        guard = self.launch("import os,time; os.write(1,b'{\\\"state\\\":'); time.sleep(3)")
        start = time.monotonic()
        with self.assertRaisesRegex(PgFenceBlocked, 'timed out'):
            guard._read(0.3)
        self.assertLess(time.monotonic() - start, 1.4)

    def test_split_frame_is_accepted_when_completed_within_deadline(self):
        raw = json.dumps({
            'state': 'held', 'ledger_present': True,
            'applied': 0, 'fingerprint': EMPTY_LEDGER_SHA256,
        }, separators=(',', ':')) + '\n'
        code = (
            'import os,time;'
            f'p={raw.encode()!r};'
            'os.write(1,p[:15]);time.sleep(.08);'
            'os.write(1,p[15:]);time.sleep(1)'
        )
        self.assertEqual(self.launch(code)._read(1)['fingerprint'], EMPTY_LEDGER_SHA256)

    def test_frame_with_trailing_bytes_is_rejected(self):
        raw = json.dumps({
            'state': 'held', 'ledger_present': True,
            'applied': 0, 'fingerprint': EMPTY_LEDGER_SHA256,
        }, separators=(',', ':')) + '\nEXTRA'
        code = f'import os,time;os.write(1,{raw.encode()!r});time.sleep(1)'
        with self.assertRaisesRegex(PgFenceBlocked, 'extra guard protocol bytes'):
            self.launch(code)._read(1)

    def test_oversized_frame_is_rejected(self):
        code = "import os,time;os.write(1,b'x'*2048);time.sleep(1)"
        with self.assertRaisesRegex(PgFenceBlocked, 'oversized'):
            self.launch(code)._read(1)

    def test_early_eof_is_not_a_valid_acknowledgement(self):
        guard = self.launch("import os;os.write(1,b'partial')")
        with self.assertRaises(PgFenceBlocked):
            guard._read(1)


if __name__ == '__main__':
    unittest.main()
