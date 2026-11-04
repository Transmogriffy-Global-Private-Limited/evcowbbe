"""Regression gates for releases built under a private administrator umask."""
from __future__ import annotations

import importlib.util
import os
import stat
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


OPERATOR = Path(__file__).with_name("rehost-evcowbbe")
loader = SourceFileLoader("cow_rehost_umask_under_test", str(OPERATOR))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class ReleaseBuildUmask(unittest.TestCase):
    def test_private_caller_umask_creates_traversable_release_and_restores(self):
        original = os.umask(0o077)
        try:
            with tempfile.TemporaryDirectory() as folder:
                def fake_build(revision):
                    target = Path(folder) / revision
                    target.mkdir(mode=0o755)
                    return target

                with patch.object(module, "_build_with_release_umask", side_effect=fake_build):
                    result = module.build("a" * 40)
                self.assertEqual(stat.S_IMODE(result.stat().st_mode), 0o755)
                current = os.umask(0o077)
                os.umask(current)
                self.assertEqual(current, 0o077)
        finally:
            os.umask(original)

    def test_caller_umask_restored_even_when_build_fails(self):
        original = os.umask(0o077)
        try:
            with patch.object(
                module, "_build_with_release_umask", side_effect=RuntimeError("failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    module.build("a" * 40)
            current = os.umask(0o077)
            os.umask(current)
            self.assertEqual(current, 0o077)
        finally:
            os.umask(original)

    def test_service_import_guard_is_present_after_revision_guard(self):
        source = OPERATOR.read_text()
        self.assertIn("RELEASE_NOT_IMPORTABLE_BY_SERVICE_ACCOUNT", source)
        self.assertLess(
            source.index("ensure_service_revision_readable(rev)\n        # The revision marker"),
            source.index("service_import = cmd("),
        )

    def test_release_build_has_no_ruff_gate(self):
        source = OPERATOR.read_text()
        self.assertNotIn("ruff.log", source)
        self.assertNotIn(".venv/bin/ruff", source)
        self.assertIn("unit-tests.log", source)
        self.assertIn("RELEASE_NOT_IMPORTABLE_BY_SERVICE_ACCOUNT", source)
