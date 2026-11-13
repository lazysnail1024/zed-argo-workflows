"""Negative controls for evidence acceptance; these do not simulate product coverage."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
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
sys.path.insert(0, str(ROOT / 'tests/e2e'))
try:
    legacy = load('effectiveness_legacy', ROOT / 'tests/e2e/legacy_mutations.py')
finally:
    sys.path.pop(0)


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

    def go_oracle_events(self, failed=False):
        selected = next(item for item in legacy.LEGACY_MUTATIONS if item.name == 'query-snapshot-bytes')
        oracle = selected.go_test
        parent = oracle.test.split('/')[0]
        events = [{'Action': 'start', 'Package': oracle.package},
                  {'Action': 'run', 'Package': oracle.package, 'Test': parent},
                  {'Action': 'run', 'Package': oracle.package, 'Test': oracle.test}]
        if failed:
            events.append({'Action': 'output', 'Package': oracle.package, 'Test': oracle.test,
                           'OutputType': 'error',
                           'Output': '    facts_store_test.go:84: ' + oracle.failure + '\n'})
        outcome = 'fail' if failed else 'pass'
        events.extend({'Action': outcome, 'Package': oracle.package, 'Test': name}
                      for name in (oracle.test, parent))
        events.append({'Action': outcome, 'Package': oracle.package})
        return selected, events

    def test_go_oracle_accepts_only_named_semantic_failure(self):
        before_item, before_events = self.go_oracle_events()
        selected, after_events = self.go_oracle_events(failed=True)
        encode = lambda events: '\n'.join(json.dumps(event) for event in events)
        before_rows = mutation.validate_go_test_events(before_item, encode(before_events), {'exitCode': 0})
        after_rows = mutation.validate_go_test_events(selected, encode(after_events), {'exitCode': 1})
        before = {row['case']: row for row in before_rows}
        after = {row['case']: row for row in after_rows}
        self.assertTrue(mutation.classify(selected, before, after, {'exitCode': 1})['detected'])
        self.assertFalse(mutation.classify(selected, before, before, {'exitCode': 0})['detected'])

    def test_go_oracle_rejects_infrastructure_and_unrelated_failures(self):
        selected, control = self.go_oracle_events(failed=True)
        oracle = selected.go_test
        parent = oracle.test.split('/')[0]
        for damage in ('missing-run', 'missing-end', 'extra-test', 'duplicate-end', 'wrong-package',
                       'other-assertion', 'other-test-output', 'log-only', 'no-error-type', 'panic', 'timeout', 'signal', 'exit-two',
                       'compile-error', 'non-json', 'wrong-exit', 'package-only-failure'):
            events = copy.deepcopy(control)
            receipt = {'exitCode': 1}
            stderr = ''
            if damage == 'missing-run':
                events = [event for event in events if not (event.get('Test') == oracle.test and event['Action'] == 'run')]
            elif damage == 'missing-end':
                events = [event for event in events if not (event.get('Test') == oracle.test and event['Action'] == 'fail')]
            elif damage == 'extra-test':
                events[1:1] = [{'Action': action, 'Package': oracle.package, 'Test': 'TestUnselected'}
                               for action in ('run', 'pass')]
            elif damage == 'duplicate-end':
                events.insert(-1, {'Action': 'fail', 'Package': oracle.package, 'Test': oracle.test})
            elif damage == 'wrong-package':
                events[2]['Package'] = './internal/unselected'
            elif damage == 'other-assertion':
                next(event for event in events if event['Action'] == 'output')['Output'] = 'unrelated assertion failed\n'
            elif damage == 'other-test-output':
                next(event for event in events if event['Action'] == 'output')['Test'] = parent
            elif damage == 'log-only':
                next(event for event in events if event['Action'] == 'output')['OutputType'] = 'log'
            elif damage == 'no-error-type':
                next(event for event in events if event['Action'] == 'output').pop('OutputType')
            elif damage == 'panic':
                stderr = 'panic: unrelated runtime failure\n'
            elif damage == 'timeout':
                receipt['timedOut'] = True
            elif damage == 'signal':
                receipt['exitCode'] = -9
            elif damage == 'exit-two':
                receipt['exitCode'] = 2
            elif damage == 'compile-error':
                events = [{'Action': 'fail', 'Package': oracle.package}]
                stderr = 'undefined: sourceIdentifier\n'
            elif damage == 'wrong-exit':
                receipt['exitCode'] = 0
            elif damage == 'package-only-failure':
                for event in events:
                    if event.get('Test') and event['Action'] == 'fail':
                        event['Action'] = 'pass'
            output = '\n'.join(json.dumps(event) for event in events)
            if damage == 'non-json':
                output += '\ncompiler emitted an unstructured error'
            with self.subTest(damage=damage), self.assertRaises(mutation.GateError):
                mutation.validate_go_test_events(selected, output, receipt, stderr)

    def test_go_oracle_preserves_native_exit_before_json_conversion(self):
        # A real TestMain can emit a complete semantic FAIL and then exit 2.
        # test2json's child mode normalizes both native exits 1 and 2 to 1;
        # checking only that wrapper's status would accept a false kill.
        go = shutil.which('go')
        self.assertIsNotNone(go, 'the CI evidence gate requires the pinned Go toolchain')
        package = self.root / 'internal/analysis'
        package.mkdir(parents=True)
        (self.root / 'go.mod').write_text('module evidence.invalid/oracle\n\ngo 1.27.3\n')
        (package / 'facts_store_test.go').write_text('''package analysis
import ("os"; "testing")
func TestEvidenceIdentity(t *testing.T) {
    t.Run("text", func(t *testing.T) {
        if os.Getenv("WF_EVIDENCE_FAIL") == "1" { t.Fatal("different bytes reused") }
    })
}
func TestMain(m *testing.M) {
    code := m.Run()
    if os.Getenv("WF_EVIDENCE_EXIT_TWO") == "1" { os.Exit(2) }
    os.Exit(code)
}
''')
        binary = self.root / ('oracle.test.exe' if os.name == 'nt' else 'oracle.test')
        env = os.environ | {'GOTOOLCHAIN': 'local', 'GOWORK': 'off', 'GOPROXY': 'off',
                            'WF_EVIDENCE_FAIL': '0', 'WF_EVIDENCE_EXIT_TWO': '0'}
        built = subprocess.run([go, 'test', '-c', '-o', str(binary), './internal/analysis'],
                               cwd=self.root, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        selected = mutation.Mutation('evidence-native-exit', 'audit', 'evidence verifier fixture',
            'unused.go', (), 'internal/analysis/facts_store_test.go',
            (('TestEvidenceIdentity/text', 'fresh_bytes'),), timeout=20,
            go_test=mutation.GoTestOracle('./internal/analysis', 'TestEvidenceIdentity/text',
                                         'different bytes reused'))
        receipt, baseline = mutation.run_go_test(selected, go, self.root, binary, self.root / 'positive', env, [])
        self.assertEqual(receipt['exitCode'], 0)
        receipt, negative = mutation.run_go_test(selected, go, self.root, binary, self.root / 'semantic',
                                                env | {'WF_EVIDENCE_FAIL': '1'}, [])
        self.assertEqual(receipt['exitCode'], 1)
        self.assertTrue(mutation.classify(selected, baseline, negative, receipt)['detected'])
        broken = self.root / 'exit-two'
        with self.assertRaisesRegex(mutation.GateError, 'exit normally'):
            mutation.run_go_test(selected, go, self.root, binary, broken,
                                 env | {'WF_EVIDENCE_FAIL': '1', 'WF_EVIDENCE_EXIT_TWO': '1'}, [])
        self.assertEqual(json.loads((broken / 'native/command.json').read_text())['exitCode'], 2)
        self.assertFalse((broken / 'run/results.json').exists(), 'abnormal native exits cannot become semantic rows')

    def test_all_registered_mutations_match_current_source_once(self):
        registry = mutation.MUTATIONS + legacy.LEGACY_MUTATIONS
        names = [item.name for item in registry]
        self.assertEqual(len(names), len(set(names)))
        for item in registry:
            with self.subTest(name=item.name):
                source = (ROOT / item.file).read_text()
                suite = ROOT / item.suite if item.go_test else ROOT / 'tests/e2e' / item.suite
                self.assertTrue(suite.is_file())
                if item.go_test:
                    self.assertTrue(item.suite.endswith('_test.go'))
                    self.assertEqual(suite.parent, ROOT / item.go_test.package)
                    self.assertIn('func ' + item.go_test.test.split('/')[0] + '(t *testing.T)', suite.read_text())
                    self.assertTrue(item.go_test.failure)
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

    def test_query_identity_requires_current_readiness_and_wire_helpers(self):
        binary = self.root / 'synthetic-binary'
        binary.write_bytes(b'synthetic metadata verifier fixture; never executed')
        manifest = self.root / 'manifest.json'
        for name in ('wf-external-candidate-density', 'wf-inherited-parameter-density', 'wf-exit-query-density',
                     'wf-query-handoff-retention'):
            evidence = {'testSha256': ci.sha(ROOT / 'tests/e2e' / (name + '.py')),
                        'binarySha256': ci.sha(binary),
                        'helperSha256': ci.sha(ROOT / 'tests/e2e/wf-audit-gaps.py'),
                        'queryReadinessHelperSha256': ci.sha(ROOT / 'tests/e2e/query_density_support.py')}
            manifest.write_text(json.dumps(evidence))
            ci.verify_consumer_identity(name, self.root, binary)
            for key in ('helperSha256', 'queryReadinessHelperSha256'):
                for damage in ('missing', 'mismatched'):
                    damaged = dict(evidence)
                    if damage == 'missing':
                        damaged.pop(key)
                    else:
                        damaged[key] = '0' * 64
                    with self.subTest(consumer=name, helper=key, damage=damage):
                        manifest.write_text(json.dumps(damaged))
                        with self.assertRaisesRegex(ValueError, 'selected query helper'):
                            ci.verify_consumer_identity(name, self.root, binary)

    def test_self_reported_partial_worker_selection_is_rejected(self):
        # Previously the runner trusted expectedCases written by the same worker
        # which selected the subset, accepting a one-case replacement of three.
        (self.root / 'manifest.json').write_text(json.dumps({'expectedCases': ['path-without-literal']}))
        self.write([{'case': 'path-without-literal', 'exitCode': 0, 'command': ['python', '--worker']}])
        with self.assertRaisesRegex(ValueError, 'empty or incomplete'):
            ci.consumer_results('wf-argument-supply', self.root)


if __name__ == '__main__':
    unittest.main()
