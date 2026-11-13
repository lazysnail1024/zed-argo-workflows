"""Every first-party test entry needs an explicit maintained execution route."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


def first_party_tests():
    paths = set((ROOT / 'tests').rglob('*.py'))
    paths.update((ROOT / 'editors/zed/tests').glob('*.py'))
    for directory in ('cmd', 'compat', 'internal'):
        paths.update((ROOT / directory).rglob('*_test.go'))
    paths.update(path for path in (ROOT / 'editors/zed/src').rglob('*.rs')
                 if '#[test]' in path.read_text(encoding='utf-8'))
    return {path.relative_to(ROOT).as_posix() for path in paths}


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.inventory = json.loads((ROOT / 'tests/ci/inventory.json').read_text())

    def test_every_test_file_has_one_current_route(self):
        classified = []
        for group in self.inventory['groups']:
            self.assertIn(group['role'], {'consumer', 'unit', 'driver', 'helper', 'builder', 'alias'})
            self.assertTrue(group['contract'])
            self.assertTrue(group['gate'])
            self.assertTrue(group['files'])
            for route in group['via']:
                self.assertTrue((ROOT / route).is_file(), route)
            classified.extend(group['files'])
        self.assertEqual(len(classified), len(set(classified)), 'a test entry has multiple primary classifications')
        self.assertEqual(set(classified), first_party_tests(),
                         'new, removed or renamed test files need an inventory route; no silent orphan tests')

    def test_native_and_semantic_selections_match_inventory(self):
        spec = importlib.util.spec_from_file_location('inventory_ci', ROOT / 'tests/ci/run.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        groups = {group['name']: group for group in self.inventory['groups']}
        for name, selected in (('native', runner.NATIVE), ('semantic', runner.EXTENDED)):
            self.assertEqual(groups[name]['files'], ['tests/e2e/' + value + '.py' for value in selected])
        contracts = json.loads((ROOT / 'tests/ci/contracts.json').read_text())['consumers']
        ordinary = set(runner.NATIVE) | set(runner.EXTENDED)
        ordinary -= set(runner.AGGREGATED) | {'wf-multi-version'}
        self.assertFalse(ordinary - set(contracts), 'ordinary consumers need parent-owned complete selections')
        workflow = (ROOT / '.github/workflows/native.yml').read_text()
        self.assertIn("python -m unittest discover -s tests/ci -p 'test_*.py' -v", workflow)


if __name__ == '__main__':
    unittest.main()
