#!/usr/bin/env python3
"""Read-only modeled candidate versions: independent boundary fixtures, CLI and LSP.

Expectations below precede the implementation and come from pinned upstream
validateArgumentsValues, ProcessArgs, ValidateTaskResults and resolveAllVariables
changes. No production predicate, branch-to-release metadata, or captured output
is used to manufacture semantic expected sets. The existing multi-version suite
retains the complete catalog/kind sweep. This consumer targets the nine modeled
semantic boundaries, real auto/exact behavior, and explanation authority.
"""
import copy
import hashlib
import importlib.util
import json
import pathlib
import sys
import time
import traceback

HELPER = pathlib.Path(__file__).with_name('wf-audit-gaps.py')
spec = importlib.util.spec_from_file_location('wire', HELPER)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCK = json.loads((ROOT / 'compat/catalog.lock.json').read_bytes())
VERSIONS = [v['version'] for v in LOCK['versions']]
BASE = {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Workflow',
        'metadata': {'name': 'example', 'namespace': 'argo'},
        'spec': {'entrypoint': 'work', 'templates': [{'name': 'work', 'container': {'image': 'alpine'}}]}}
LABEL = 'Candidates under modeled static constraints'
RULES = ['argument-value-from', 'template-empty-arguments', 'argument-configmap',
         'dag-omitted', 'workflow-parameters-json', 'external-exit',
         'argument-overwrites-default', 'argument-value-from-restriction', 'absent-output-marker']


def text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def config(version='auto'):
    return 'schemaVersion: 1\ntargetVersion: "' + version + '"\nreferencePolicy: closed\n'


def fixture(rule):
    value = copy.deepcopy(BASE)
    argument = {'name': 'p'}
    files = {}
    if rule == 'argument-value-from':
        argument['valueFrom'] = {'supplied': {}}
    elif rule == 'template-empty-arguments':
        value['kind'] = 'WorkflowTemplate'
    elif rule == 'argument-configmap':
        argument['valueFrom'] = {'configMapKeyRef': {'name': 'settings', 'key': 'p'}}
        value['spec']['templates'][0]['inputs'] = {'parameters': [{'name': 'p'}]}
    elif rule == 'dag-omitted':
        value['spec']['entrypoint'] = 'main'
        value['spec']['templates'].append({'name': 'main', 'dag': {'tasks': [
            {'name': 'first', 'template': 'work'}, {'name': 'second', 'template': 'work', 'depends': 'first.Omitted'}]}})
        return value, files
    elif rule == 'workflow-parameters-json':
        argument['value'] = 'one'
        value['spec']['templates'][0]['container']['args'] = ['{{workflow.parameters.json}}']
    elif rule == 'external-exit':
        library = copy.deepcopy(BASE)
        library['kind'] = 'WorkflowTemplate'
        library['metadata']['name'] = 'library'
        library['spec']['templates'].append({'name': 'cleanup', 'container': {'image': 'alpine'}})
        files['library.yaml'] = text(library)
        value['spec'] = {'workflowTemplateRef': {'name': 'library'}, 'onExit': 'cleanup'}
        return value, files
    elif rule == 'argument-overwrites-default':
        argument['valueFrom'] = {'event': 'payload.p'}
        value['spec']['templates'][0]['inputs'] = {'parameters': [{'name': 'p', 'default': 'fallback'}]}
    elif rule == 'argument-value-from-restriction':
        argument['valueFrom'] = {'path': '/tmp/value'}
    elif rule == 'absent-output-marker':
        argument['value'] = '__argo-internal.absent-optional-output__'
        value['spec']['templates'][0]['inputs'] = {'parameters': [{'name': 'p'}]}
    else:
        raise ValueError(rule)
    value['spec']['arguments'] = {'parameters': [argument]}
    return value, files


# Expected semantic membership and diagnostic codes are fixed independently of
# implementation and deliberately include both sides of each backport branch.
BOUNDARIES = {
    'argument-value-from': ({'3.1.15': False, '3.2.0': True}, 'WF_ARGUMENT_VALUE_INVALID'),
    'template-empty-arguments': ({'3.2.3': False, '3.2.4': True}, 'WF_ARGUMENT_VALUE_INVALID'),
    'argument-configmap': ({'3.2.4': False, '3.2.5': True}, 'WF_ARGUMENT_MISSING'),
    'dag-omitted': ({'3.3.8': False, '3.3.9': True}, 'WF_DEPENDS_SYNTAX'),
    'workflow-parameters-json': ({'3.4.1': False, '3.4.2': True}, 'WF_REFERENCE_SCOPE'),
    'external-exit': ({'3.4.5': False, '3.4.6': True}, 'WF_LOCAL_TEMPLATE_MISSING'),
    'argument-overwrites-default': ({'3.4.9': True, '3.4.10': False}, 'WF_ARGUMENT_MISSING'),
    'argument-value-from-restriction': ({'3.4.16': True, '3.4.17': False, '3.5.4': True, '3.5.5': False}, 'WF_ARGUMENT_VALUE_INVALID'),
    'absent-output-marker': ({'3.7.15': True, '3.7.16': False, '4.0.0': True, '4.0.6': True, '4.0.7': False, '4.1.0': False}, 'WF_ARGUMENT_MISSING'),
}


def disk(name, value, extra=None, version='auto'):
    raw = value if isinstance(value, str) else text(value)
    dest, work = m.setup(name, {'case.yaml': raw, **(extra or {})}, config(version))
    output = m.command(dest, 'explanation', [m.BINARY, 'explain-config', '--root', work, '--file', 'case.yaml'])
    report = json.loads(output.stdout) if output.returncode == 0 else {}
    status = report.get('analysis', {})
    explanation = status.get('compatibility', {})
    resources = explanation.get('resources', [])
    return dest, work, status, explanation, resources[0] if resources else {}


def boundary(rule):
    value, files = fixture(rule)
    expected, code = BOUNDARIES[rule]
    dest, work, status, explanation, resource = disk(rule, value, files)
    semantics = resource.get('semanticCandidates', [])
    exclusions = resource.get('exclusions', [])
    auto_code, auto = m.cli(dest, work, 'auto')
    exacts = {}
    for version, accepted in expected.items():
        (work / '.argo-workflows-ls.yaml').write_bytes(config(version).encode())
        exit_code, report = m.cli(dest, work, 'exact-' + version)
        exacts[version] = {'exitCode': exit_code, 'report': report,
                         'modeledAccepted': not any(d['severity'] == 'Error' and d['code'].startswith('WF_') for d in report['diagnostics'])}
    (work / '.argo-workflows-ls.yaml').write_bytes(config().encode())
    return {
        'boundary_candidates': bool(resource) and all((v in semantics) == yes for v, yes in expected.items()),
        'real_exact_boundaries': all(exacts[v]['modeledAccepted'] == yes for v, yes in expected.items()),
        'exclusion_guard': any(e.get('code') == code and rule in e.get('ruleIds', []) and e.get('location', {}).get('uri') == (work / 'case.yaml').as_uri() for e in exclusions),
        'auto_unchanged': auto_code == 0 and not any(d['severity'] == 'Error' for d in auto['diagnostics']),
        'bounded_work': 0 < resource.get('work', {}).get('semanticProfiles', 0) <= 12 and 0 < resource.get('work', {}).get('schemaBranches', 0) <= 18,
        'input_unchanged': (work / 'case.yaml').read_bytes() == text(value).encode(),
    }, {'status': status, 'auto': auto, 'exacts': exacts, 'expectedMembership': expected}


def schema_sets():
    old = copy.deepcopy(BASE); old['spec']['podPriority'] = 1
    new = copy.deepcopy(BASE); new['spec']['podResources'] = {'requests': {'cpu': '1'}}
    mixed = copy.deepcopy(new); mixed['spec']['podPriority'] = 1
    results = {}
    for name, value in [('old', old), ('new', new), ('mixed', mixed)]:
        _, _, _, _, resource = disk('schema-' + name, value)
        results[name] = resource
    return {
        'introduced': '4.1.0' in results['new'].get('schemaCandidates', []) and '4.0.0' not in results['new'].get('schemaCandidates', []),
        'removed': '3.0.0' in results['old'].get('schemaCandidates', []) and '4.1.0' not in results['old'].get('schemaCandidates', []),
        'whole_document': results['mixed'].get('schemaCandidates') == [] and results['mixed'].get('candidates') == [] and results['mixed'].get('evaluated') is True,
        'schema_exclusion': any(e.get('code') == 'SCHEMA_NO_MATCH' for e in results['mixed'].get('exclusions', [])),
    }, results


def conflict():
    inner, _ = fixture('argument-value-from-restriction')
    value = {'apiVersion': inner['apiVersion'], 'kind': 'CronWorkflow', 'metadata': inner['metadata'],
             'spec': {'schedules': ['0 * * * *'], 'workflowSpec': inner['spec']}}
    dest, work, status, explanation, resource = disk('joint-empty', value)
    code, auto = m.cli(dest, work, 'auto')
    schemas = resource.get('schemaCandidates', [])
    semantic = resource.get('semanticCandidates', [])
    return {
        'schema_nonempty': len(schemas) == 61 and schemas[0] == '3.6.0' and schemas[-1] == '4.1.6',
        'semantic_nonempty': len(semantic) == 45 and '3.4.16' in semantic and '3.5.4' in semantic,
        'joint_empty': resource.get('candidates') == [] and resource.get('evaluated') is True,
        'honest_label': explanation.get('label') == LABEL and 'controller-execution' in explanation.get('unmodeled', []),
        'auto_still_passes': code == 0 and auto['diagnostics'] == [],
        'rules_independent_of_crd': all(next(v['schemaSha256'] for v in LOCK['versions'] if v['version'] == a) == next(v['schemaSha256'] for v in LOCK['versions'] if v['version'] == b) for a, b in [('3.4.16', '3.4.17'), ('3.5.4', '3.5.5')]),
    }, {'status': status, 'auto': auto}


def unknowns():
    dynamic = copy.deepcopy(BASE); dynamic['spec']['entrypoint'] = '{{workflow.parameters.template}}'
    _, _, _, exp_dynamic, res_dynamic = disk('dynamic', dynamic)
    _, _, status_bad, exp_bad, _ = disk('damaged', 'apiVersion: [\n')
    external, files = fixture('external-exit')
    _, _, _, exp_external, res_external = disk('external-unknown', external)
    calls = copy.deepcopy(BASE)
    calls['spec']['entrypoint'] = 'main'
    calls['spec']['templates'].append({'name': 'main', 'steps': [[{'name': 'first', 'template': 'work', 'arguments': {'parameters': [{'name': 'unused', 'valueFrom': {'path': '/tmp/value'}}]}}]]})
    _, _, _, exp_call, res_call = disk('call-form-unknown', calls)
    return {
        'dynamic_unknown': res_dynamic.get('modeledComplete') is False and any(u.get('reasonCode') == 'DYNAMIC_REFERENCE' for u in res_dynamic.get('unknowns', [])),
        'dynamic_keeps_possible': bool(res_dynamic.get('candidates')) and res_dynamic.get('evaluated') is True,
        'damaged_unevaluated': exp_bad.get('evaluated') is False and bool(exp_bad.get('notEvaluated')) and status_bad.get('completeness', {}).get('document') is False,
        'external_unknown': res_external.get('modeledComplete') is False and bool(res_external.get('unknowns')),
        'call_forms_unknown': res_call.get('modeledComplete') is False and any(u.get('reasonCode') == 'UNMODELED_CALL_ARGUMENT_FORMS' for u in res_call.get('unknowns', [])),
    }, {'dynamic': exp_dynamic, 'damaged': exp_bad, 'external': exp_external, 'callForms': exp_call}


def separate_resources():
    old = copy.deepcopy(BASE); old['spec']['podPriority'] = 1
    new = copy.deepcopy(BASE); new['metadata']['name'] = 'second'; new['spec']['podResources'] = {'requests': {'cpu': '1'}}
    _, _, _, explanation, _ = disk('separate-resources', text(old) + '---\n' + text(new))
    resources = explanation.get('resources', [])
    return {
        'separate_candidates': len(resources) == 2 and all(r.get('candidates') for r in resources) and not set(resources[0]['candidates']) & set(resources[1]['candidates']),
        'distinct_identity': len(resources) == 2 and [r.get('documentIndex') for r in resources] == [0, 1] and [r.get('name') for r in resources] == ['example', 'second'],
        'no_workspace_intersection': 'candidates' not in explanation,
    }, explanation


def schema_only():
    value = {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'WorkflowTaskSet',
             'metadata': {'name': 'taskset'}, 'spec': {'tasks': {}}}
    _, _, _, explanation, resource = disk('schema-only', value)
    return {
        'introduced_kind': '3.1.15' not in resource.get('schemaCandidates', []) and '3.2.0' in resource.get('schemaCandidates', []),
        'absence_explained': any(e.get('code') == 'SCHEMA_UNAVAILABLE' and '3.1.15' in e.get('versions', []) for e in resource.get('exclusions', [])),
        'semantics_unknown': resource.get('modeledComplete') is False and any(u.get('reasonCode') == 'UNMODELED_RESOURCE_SEMANTICS' for u in resource.get('unknowns', [])),
    }, explanation


def incomplete_external():
    value, files = fixture('external-exit')
    value['spec'].pop('onExit')
    library = json.loads(files['library.yaml'])
    library['spec']['templates'][0]['inputs'] = {'parameters': [{'name': 'p'}]}
    files['library.yaml'] = text(library)
    files['broken.yaml'] = 'apiVersion: [\n'
    _, _, status, explanation, resource = disk('incomplete-external', value, files)
    return {
        'scope_incomplete': status.get('completeness', {}).get('workspace') is False,
        'incomplete_unknown': resource.get('modeledComplete') is False and any(u.get('reasonCode') == 'INDEX_INCOMPLETE' for u in resource.get('unknowns', [])),
        'no_partial_exclusion': '3.6.0' in resource.get('semanticCandidates', []) and not any(e.get('code') == 'WF_ARGUMENT_MISSING' for e in resource.get('exclusions', [])),
    }, explanation


def causal_rules():
    value = copy.deepcopy(BASE); value['kind'] = 'WorkflowTemplate'
    value['spec']['templates'][0]['container']['args'] = ['{{workflow.parameters}}']
    _, _, _, local, loc = disk('causal-template-aggregate', value)
    caller, files = fixture('external-exit'); caller['spec'].pop('onExit')
    library = json.loads(files['library.yaml'])
    library['spec']['templates'][0]['container']['args'] = ['{{workflow.parameters.missing}}']
    _, _, _, reachable, reach = disk('causal-reachable', caller, {'library.yaml': text(library)})
    library['spec']['templates'][0]['container'].pop('args')
    library['spec']['templates'][1]['container']['args'] = ['{{workflow.parameters.missing}}']
    _, _, _, unvisited, unvisit = disk('causal-unvisited', caller, {'library.yaml': text(library)})
    return {
        'template_boundary_id': any(e.get('code') == 'WF_REFERENCE_SCOPE' and 'workflow-parameters-json' in e.get('ruleIds', []) and '3.4.1' in e.get('versions', []) for e in loc.get('exclusions', [])),
        'invariant_no_false_gate': any(e.get('code') == 'WF_REFERENCE_SCOPE' for e in reach.get('exclusions', [])) and all(not set(e.get('ruleIds', [])) & {'workflow-parameters-json', 'external-exit'} for e in reach.get('exclusions', []) if e.get('code') == 'WF_REFERENCE_SCOPE'),
        'traversal_boundary': '3.4.5' in unvisit.get('semanticCandidates', []) and '3.4.6' not in unvisit.get('semanticCandidates', []),
        'traversal_id': any(e.get('code') == 'WF_REFERENCE_SCOPE' and e.get('ruleIds') == ['external-exit'] for e in unvisit.get('exclusions', [])),
    }, {'local': local, 'reachable': reachable, 'unvisited': unvisited}


def identity_and_pin():
    value, _ = fixture('argument-value-from-restriction')
    dest, work, status, explanation, resource = disk('identity-and-pin', value, version='3.5.5')
    provenance = ROOT / 'compat/modeled-rules.json'
    metadata = json.loads(provenance.read_bytes()) if provenance.exists() else {}
    checked = m.command(dest, 'provenance-check', [sys.executable, ROOT / 'scripts/check-modeled-rules.py'])
    check_report = json.loads(checked.stdout) if checked.returncode == 0 else {}
    return {
        'catalog_identity': explanation.get('catalogHash') == m.sha(ROOT / 'compat/catalog.lock.json'),
        'rule_identity': provenance.exists() and explanation.get('modeledRulesHash') == m.sha(provenance),
        'evidence_scope': metadata.get('coverage', {}).get('kind') == 'boundary-samples' and metadata.get('coverage', {}).get('allCatalogRuntimeSourcesAudited') is False,
        'selected_pin': resource.get('selectedTarget') == {'version': '3.5.5', 'schemaAccepted': True, 'modeledAccepted': False},
        'covered_rules': set(RULES) <= set(explanation.get('modeledRuleIds', [])),
        'maintenance_check': checked.returncode == 0 and check_report.get('valid') is True and check_report.get('catalogVersionCount') == 146 and check_report.get('allCatalogRuntimeSourcesAudited') is False,
        'disk_identity': status.get('inputIdentity', {}).get('sha256') == hashlib.sha256(text(value).encode()).hexdigest() and status['inputIdentity']['source'] == 'disk',
    }, status


def editor():
    dest, work = m.setup('editor', {'case.yaml': text(BASE)}, config())
    client = m.LSP(dest, work)
    try:
        changed = copy.deepcopy(BASE); changed['spec']['podResources'] = {'requests': {'cpu': '1'}}
        raw = text(changed); path = work / 'case.yaml'
        client.open(path, raw, 7)
        params = {'textDocument': {'uri': path.as_uri()}}
        response = client.request('argo-workflows/explainAnalysis', params)
        status = response.get('result', {})
        resource = next(iter(status.get('compatibility', {}).get('resources', [])), {})
        actions = client.request('textDocument/codeAction', {**params, 'range': {'start': {'line': 1, 'character': 0}, 'end': {'line': 1, 'character': 0}}, 'context': {'diagnostics': [], 'only': ['source.explainAnalysis']}})
        action = next((a for a in actions.get('result', []) if a.get('command', {}).get('command') == 'argo-workflows.explainAnalysis'), {})
        resolved = client.request('codeAction/resolve', action) if action else {}
        executed = client.request('workspace/executeCommand', resolved['result']['command']) if resolved.get('result', {}).get('command') else {}
        if 'result' in executed:
            client.wait(lambda event: event.get('method') == 'window/showMessage')
        messages = [e['params']['message'] for e in client.received if e.get('method') == 'window/showMessage']
        client.change(path, text(BASE), 8)
        stale = client.request('workspace/executeCommand', action['command']) if action else {}
        return {
            'unsaved_candidates': '4.1.0' in resource.get('candidates', []) and '3.6.0' not in resource.get('candidates', []),
            'buffer_identity': status.get('inputIdentity', {}).get('source') == 'buffer' and status['inputIdentity'].get('version') == 7 and status['inputIdentity'].get('sha256') == hashlib.sha256(raw.encode()).hexdigest(),
            'visible_label': bool(messages) and LABEL in messages[-1] and 'candidate' in messages[-1].lower(),
            'stale_refused': stale.get('error', {}).get('data', {}).get('reasonCode') == 'STALE_INPUT',
            'disk_unchanged': path.read_bytes() == text(BASE).encode(),
        }, {'status': status, 'action': action, 'executed': executed, 'messages': messages, 'stale': stale}
    finally:
        client.close()


CASES = [(rule, lambda rule=rule: boundary(rule)) for rule in RULES] + [
    ('schema-boundaries', schema_sets), ('joint-empty', conflict), ('unknowns', unknowns),
    ('separate-resources', separate_resources), ('identity-and-pin', identity_and_pin), ('editor', editor),
    ('schema-only', schema_only), ('incomplete-external', incomplete_external), ('causal-rules', causal_rules),
]
for name, run in CASES:
    try:
        assertions, details = run()
        m.result(name, assertions, details)
    except Exception as error:
        m.result(name, {'completed': False}, {'error': repr(error), 'traceback': traceback.format_exc()})
m.save(m.OUT / 'manifest.json', {'command': sys.argv, 'pythonVersion': sys.version,
    'binarySha256': m.sha(m.BINARY), 'testSha256': m.sha(pathlib.Path(__file__)), 'helperSha256': m.sha(HELPER),
    'expectedCases': [name for name, _ in CASES],
    'inputHashes': {str(p.relative_to(m.OUT)): m.sha(p) for p in m.OUT.rglob('*') if p.is_file() and '/input/' in str(p)},
    'scope': 'CLI/stdio boundary evidence; not controller execution or GUI acceptance'})
raise SystemExit(0 if len(m.RESULTS) == len(CASES) and all(row['pass'] for row in m.RESULTS) else 1)
