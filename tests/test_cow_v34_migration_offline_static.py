"""Static guards for the migration-0020 and no-database parity correction.

Full Django runtime and physical PostgreSQL acceptance are separate gates.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    'cowbookingpreviewticket', 'cowcompletionreview', 'cowhandoverticket',
    'cowlocationobservation', 'cowwalletoperation', 'cowwalletrefund',
}


def test_uuid_pk_state_migration_is_append_only_six_fields():
    tree = ast.parse((ROOT / 'cow/migrations/0020_cow_uuid_pk_serialization_state.py').read_text())
    pairs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'AlterField':
            keys = {kw.arg: kw.value for kw in node.keywords}
            assert isinstance(keys['model_name'], ast.Constant)
            assert isinstance(keys['name'], ast.Constant)
            assert keys['name'].value == 'id'
            f = keys['field']
            assert isinstance(f, ast.Call) and isinstance(f.func, ast.Attribute) and f.func.attr == 'UUIDField'
            kwargs = {kw.arg: kw.value for kw in f.keywords}
            for key, value in [('serialize', False), ('primary_key', True), ('editable', False)]:
                assert isinstance(kwargs[key], ast.Constant) and kwargs[key].value is value
            pairs.append(keys['model_name'].value)
    assert len(pairs) == 6 and set(pairs) == EXPECTED
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and
                   n.func.attr in ('RunSQL', 'RunPython') for n in ast.walk(tree))


def test_offline_migration_check_calls_native_autodetector_and_blocks_connect():
    script = (ROOT / 'scripts/check_migration_parity_offline.py').read_text()
    assert 'MigrationLoader(None, ignore_no_migrations=True)' in script
    assert 'MigrationAutodetector(' in script
    assert 'BaseDatabaseWrapper.ensure_connection = no_database_connection' in script
    cli = (ROOT / 'scripts/db.py').read_text()
    assert 'offline_parity_check()' in cli
    assert 'offline_manage("makemigrations", "cow", "--check", "--dry-run", "--noinput")' not in cli
