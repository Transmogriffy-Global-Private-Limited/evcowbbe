"""Canonical module import and three-branch documentation gates; no DB access."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ('cow', 'config', 'tests', 'scripts', 'ops')
LEGACY_NAMES = ('customer_api', 'customer_contract', 'admin_controls',
                'fleet_api', 'api_contracts', 'errors')


class CleanLayout(unittest.TestCase):
    def test_legacy_root_wrappers_removed(self):
        for stem in LEGACY_NAMES:
            self.assertFalse((ROOT / 'cow' / (stem + '.py')).exists(), stem)

    def test_no_live_imports_of_removed_aliases(self):
        aliases = {'cow.' + stem for stem in LEGACY_NAMES}
        for folder in SOURCE_ROOTS:
            target = ROOT / folder
            if not target.exists():
                continue
            for path in target.rglob('*.py'):
                if 'verification' in path.parts:
                    continue
                tree = ast.parse(path.read_text(encoding='utf-8'))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotIn(node.module, aliases, path.as_posix())
                        if node.module == 'cow':
                            for item in node.names:
                                self.assertNotIn('cow.' + item.name, aliases, path.as_posix())
                    elif isinstance(node, ast.Import):
                        for item in node.names:
                            self.assertNotIn(item.name, aliases, path.as_posix())

    def test_openapi_uses_canonical_customer_contract(self):
        source = (ROOT / 'cow' / 'fe_openapi.py').read_text(encoding='utf-8')
        self.assertIn('from cow.users import contracts as c', source)
        self.assertNotIn('from cow import customer_contract', source)

    def test_current_branch_contract_is_documented(self):
        guide = (ROOT / 'docs' / 'ROLE_MODULE_ARCHITECTURE_20261105.md').read_text()
        for name in ('main', 'anubhab-work', 'abhranil-pal-evcowbbe'):
            self.assertIn('`' + name + '`', guide)
        for name in ('cow/users/', 'cow/drivers/', 'cow/admin/', 'cow/common/'):
            self.assertIn(name, guide)

    def test_central_authority_remains_unique(self):
        for role in ('users', 'drivers', 'admin', 'common'):
            self.assertFalse((ROOT / 'cow' / role / 'models.py').exists())
            self.assertFalse((ROOT / 'cow' / role / 'migrations').exists())


if __name__ == '__main__':
    unittest.main()
