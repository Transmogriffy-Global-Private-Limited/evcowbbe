"""Protect the exact legacy Ruff-debt exceptions from silent expansion."""
import hashlib
import json
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs/verification/ruff-legacy-lint-debt-20261104.json"
CHANGED = {"config/settings.py", "config/urls.py", "config/swagger_static.py", "tests/test_swagger_ui.py"}


class RuffLegacyDebtGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(BASELINE.read_text(encoding="utf-8"))
        cls.config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_exact_legacy_exception_scope(self):
        entry = self.manifest
        self.assertEqual(entry["schema"], 1)
        debts = entry["exceptions"]
        configured = self.config["tool"]["ruff"]["lint"]["per-file-ignores"]
        self.assertTrue(debts)
        self.assertEqual(set(debts), set(configured))
        self.assertFalse(CHANGED & set(debts))
        for relative, record in debts.items():
            with self.subTest(path=relative):
                self.assertNotIn("*", relative)
                self.assertFalse(relative.startswith("/"))
                self.assertNotIn("..", Path(relative).parts)
                self.assertEqual(record["codes"], sorted(set(configured[relative])))
                self.assertEqual(record["codes"], configured[relative])
                self.assertEqual(sum(record["counts"].values()), record["issues"])
                self.assertEqual(sorted(record["counts"]), record["codes"])

    def test_ignored_files_are_byte_identical_to_reviewed_baseline(self):
        for relative, record in self.manifest["exceptions"].items():
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])


if __name__ == "__main__":
    unittest.main()
