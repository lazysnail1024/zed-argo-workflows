"""Negative controls for evidence acceptance; these do not simulate product coverage."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ci = load('effectiveness_ci', ROOT / 'tests/ci/run.py')
mutation = load('effectiveness_mutation', ROOT / 'tests/e2e/mutation_support.py')
resources = load('effectiveness_resources', ROOT / 'tests/ci/resources.py')


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / 'results.json'
        self.control = [{'case': 'known-case', 'assertions': {'correctValue': True, 'naturalExit': True}, 'pass': True}]

    def write(self, rows):
        self.path.write_text(json.dumps(rows))

    def test_mutation_parent_rejects_self_selected_successful_prefix(self):
        for group in ('core', 'audit', 'contracts', 'legacy', 'resources'):
            selected = ci.mutation_selection(group)
            control = {'pass': True, 'selected': selected, 'variants': [
                {'variant': name, 'status': 'killed', 'detected': True} for name in selected]}
            self.assertEqual(ci.verify_mutation_contract(group, control), len(selected))
            prefix = copy.deepcopy(control)
            prefix['selected'] = prefix['selected'][:1]
            prefix['variants'] = prefix['variants'][:1]
            with self.subTest(group=group), self.assertRaisesRegex(ValueError, 'incomplete'):
                ci.verify_mutation_contract(group, prefix)

    def test_platform_memory_and_large_index_contracts_are_distinct(self):
        from unittest.mock import patch
        contract = json.loads((ROOT / 'tests/ci/contracts.json').read_text())['consumers']['wf-persistent-workspace']
        row = {'checks': {name: True for name in contract['rows'][0]['assertions']}, 'pass': True}
        self.write(row)
        with patch.object(ci.platform, 'system', return_value='Linux'):
            ci.verify_result_contract('wf-persistent-workspace', self.path)
            with self.assertRaises(ValueError):
                ci.verify_result_contract('wf-persistent-workspace-50000', self.path)
        row['checks'].pop('peak_rss_under_512_mib')
        self.write(row)
        with patch.object(ci.platform, 'system', return_value='Darwin'):
            ci.verify_result_contract('wf-persistent-workspace', self.path)
        with patch.object(ci.platform, 'system', return_value='Linux'), self.assertRaises(ValueError):
            ci.verify_result_contract('wf-persistent-workspace', self.path)

    def test_parent_contract_rejects_successful_prefix_and_missing_assertion(self):
        contract = json.loads((ROOT / 'tests/ci/contracts.json').read_text())['consumers']['wf2-targets']
        control = [dict(row['id'], assertions={key: True for key in row['assertions']}, **{'pass': True})
                   for row in contract['rows']]
        self.write(control)
        ci.verify_result_contract('wf2-targets', self.path)
        for damage in ('prefix', 'duplicate', 'assertion', 'case'):
            rows = copy.deepcopy(control)
            if damage == 'prefix':
                rows.pop()
            elif damage == 'duplicate':
                rows[1] = copy.deepcopy(rows[0])
            elif damage == 'assertion':
                rows[0]['assertions'].pop(next(iter(rows[0]['assertions'])))
            else:
                rows[0]['name'] = 'unselected-case'
            with self.subTest(damage=damage):
                self.write(rows)
                with self.assertRaises(ValueError):
                    ci.verify_result_contract('wf2-targets', self.path)

    def test_resource_receipt_requires_unchanged_limits_and_no_oom(self):
        snapshot = {'memory.max': str(512 * 1024 ** 2), 'memory.swap.max': '0',
                    'cpu.max': '400000 100000', 'affinity': [0, 1, 2, 3], 'cgroup': '/test',
                    'memory.events': 'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n'}
        control = {'before': snapshot, 'after': copy.deepcopy(snapshot), 'exitCode': 0, 'pass': True}
        path = self.root / 'limits.json'
        path.write_text(json.dumps(control))
        resources.verify_resource_receipt(self.root, 512)
        for damage in ('missing-after', 'oom', 'oom-kill', 'memory', 'swap', 'cpu', 'affinity', 'exit'):
            receipt = copy.deepcopy(control)
            if damage == 'missing-after':
                receipt.pop('after')
            elif damage == 'oom':
                receipt['after']['memory.events'] = snapshot['memory.events'].replace('oom 0', 'oom 1')
            elif damage == 'oom-kill':
                receipt['after']['memory.events'] = snapshot['memory.events'].replace('oom_kill 0', 'oom_kill 1')
            elif damage == 'memory':
                receipt['after']['memory.max'] = 'max'
            elif damage == 'swap':
                receipt['after']['memory.swap.max'] = '1'
            elif damage == 'cpu':
                receipt['after']['cpu.max'] = 'max 100000'
            elif damage == 'affinity':
                receipt['after']['affinity'] = [0, 1, 2]
            else:
                receipt['exitCode'] = 1
            with self.subTest(damage=damage):
                path.write_text(json.dumps(receipt))
                with self.assertRaises((ValueError, KeyError)):
                    resources.verify_resource_receipt(self.root, 512)

    def test_real_semantic_evidence_is_accepted(self):
        self.write(self.control)
        self.assertEqual(ci.validate_results(self.path), 1)
        self.assertEqual(set(mutation.read_results(self.root)), {'known-case'})

    def test_false_success_shapes_are_rejected_one_axis_at_a_time(self):
        false_shapes = {
            'empty-list': [], 'pass-only': [{'pass': True}],
            'missing-assertions': [{'case': 'known-case', 'pass': True}],
            'empty-assertions': [{'case': 'known-case', 'pass': True, 'assertions': {}}],
            'truthy-pass': [{'case': 'known-case', 'pass': 'true', 'assertions': {'correctValue': True}}],
            'truthy-assertion': [{'case': 'known-case', 'pass': True, 'assertions': {'correctValue': 'true'}}],
            'false-assertion': [{'case': 'known-case', 'pass': True, 'assertions': {'correctValue': False}}],
            'duplicate-case': self.control * 2,
        }
        for name, rows in false_shapes.items():
            with self.subTest(name=name):
                self.write(rows)
                with self.assertRaises((ValueError, mutation.GateError)):
                    ci.validate_results(self.path)
        for key in ('fatal', 'error', 'timedOut', 'harnessKilled'):
            with self.subTest(name=key):
                rows = copy.deepcopy(self.control)
                rows[0][key] = True
                self.write(rows)
                with self.assertRaises(ValueError):
                    ci.validate_results(self.path)

    def test_checks_schema_cannot_hide_a_failed_assertion(self):
        self.write([{'version': '3.6.0', 'pass': True, 'checks': {'exactCLI': False}}])
        with self.assertRaises(ValueError):
            ci.validate_results(self.path, assertion_key='checks')

    def test_mutation_requires_complete_named_failure(self):
        before = {'known-case': self.control[0]}
        after = copy.deepcopy(before)
        after['known-case']['assertions']['correctValue'] = False
        after['known-case']['pass'] = False
        result = mutation.classify_results((('known-case', 'correctValue'),), before, after, {'exitCode': 1})
        self.assertTrue(result['detected'])
        for receipt in ({'exitCode': -9}, {'exitCode': 1, 'timedOut': True}, {'exitCode': None}):
            with self.subTest(receipt=receipt), self.assertRaises(mutation.GateError):
                mutation.classify_results((('known-case', 'correctValue'),), before, after, receipt)
        self.assertFalse(mutation.classify_results((('known-case', 'correctValue'),), before, before, {'exitCode': 1})['detected'])
        with self.assertRaises(mutation.GateError):
            mutation.classify_results((('known-case', 'correctValue'),), before, {}, {'exitCode': 1})
        after['known-case']['assertions'].pop('naturalExit')
        with self.assertRaises(mutation.GateError):
            mutation.classify_results((('known-case', 'correctValue'),), before, after, {'exitCode': 1})

    def test_mutation_rejects_exception_and_process_failure(self):
        for change in ('duplicate', 'exception', 'crash', 'timeout'):
            with self.subTest(change=change):
                rows = copy.deepcopy(self.control)
                if change == 'duplicate':
                    rows += rows
                elif change == 'exception':
                    rows[0] = {'case': 'known-case', 'assertions': {'completed': False}, 'pass': False}
                else:
                    (self.root / 'native.command.json').write_text(json.dumps({'exitCode': -9 if change == 'crash' else 1, 'timedOut': change == 'timeout'}))
                self.write(rows)
                with self.assertRaises(mutation.GateError):
                    mutation.read_results(self.root)
                (self.root / 'native.command.json').unlink(missing_ok=True)

    def test_all_registered_mutations_match_current_source_once(self):
        names = [item.name for item in mutation.MUTATIONS]
        self.assertEqual(len(names), len(set(names)))
        for item in mutation.MUTATIONS:
            with self.subTest(name=item.name):
                source = (ROOT / item.file).read_text()
                self.assertTrue((ROOT / 'tests/e2e' / item.suite).is_file())
                self.assertTrue(item.required)
                for before, after in item.replacements:
                    self.assertNotEqual(before, after)
                    self.assertEqual(source.count(before), 1, item.file)
                    source = source.replace(before, after, 1)

    def test_existing_consumer_directory_is_not_reused(self):
        from argparse import Namespace
        output = self.root / 'run'
        (output / 'wf1').mkdir(parents=True)
        self.write(self.control)
        with self.assertRaisesRegex(ValueError, 'stale'):
            ci.suite(Namespace(suite='native', shard=None, output=output, binary=self.root/'unused', tools=None))

    def test_identity_requires_both_the_selected_consumer_and_binary(self):
        binary = self.root / 'synthetic-binary'
        binary.write_bytes(b'synthetic metadata verifier fixture; never executed')
        evidence = {'testSha256': ci.sha(ROOT / 'tests/e2e/wf1.py'), 'binarySha256': ci.sha(binary)}
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps(evidence))
        ci.verify_consumer_identity('wf1', self.root, binary)
        for key in evidence:
            with self.subTest(key=key):
                manifest.write_text(json.dumps(evidence | {key: '0' * 64}))
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    ci.verify_consumer_identity('wf1', self.root, binary)
        manifest.write_text(json.dumps({'testSha256': evidence['testSha256']}))
        with self.assertRaisesRegex(ValueError, 'native binary'):
            ci.verify_consumer_identity('wf1', self.root, binary)

    def test_self_reported_partial_worker_selection_is_rejected(self):
        # Previously the runner trusted expectedCases written by the same worker
        # which selected the subset, accepting a one-case replacement of three.
        (self.root / 'manifest.json').write_text(json.dumps({'expectedCases': ['path-without-literal']}))
        self.write([{'case': 'path-without-literal', 'exitCode': 0, 'command': ['python', '--worker']}])
        with self.assertRaisesRegex(ValueError, 'empty or incomplete'):
            ci.consumer_results('wf-argument-supply', self.root)


if __name__ == '__main__':
    unittest.main()
