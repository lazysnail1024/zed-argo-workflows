#!/usr/bin/env python3
"""Real stdio publications must belong to the current Workflows document."""
import importlib.util
import pathlib
import sys
import time
import traceback

spec = importlib.util.spec_from_file_location('wire', pathlib.Path(__file__).with_name('wf-audit-gaps.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FOREIGN = {
    'deployment': 'apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: web}\n',
    'application': 'apiVersion: argoproj.io/v1alpha1\nkind: Application\nspec: {}\n',
    'applicationset': 'apiVersion: argoproj.io/v1alpha1\nkind: ApplicationSet\nspec: {}\n',
    'other-group': 'apiVersion: example.com/v1\nkind: Workflow\nspec: {}\n',
    'unknown-argo-kind': 'apiVersion: argoproj.io/v1alpha1\nkind: SomethingElse\n',
    'values': 'workflow:\n  apiVersion: argoproj.io/v1alpha1\n  kind: Workflow\n',
    'comments': '# apiVersion: argoproj.io/v1alpha1\n# kind: Workflow\nreplicas: 2\n',
    'block-scalar': 'example: |\n  apiVersion: argoproj.io/v1alpha1\n  kind: Workflow\n',
    'plain': 'name: service\nenabled: true\n',
    'damaged-foreign': 'apiVersion: apps/v1\nkind: Deployment\nspec: [\n',
    'empty': '',
}
FOREIGN['mixed-foreign'] = FOREIGN['deployment'] + '---\n' + FOREIGN['application']
BROKEN = m.REN_DAG.replace('  entrypoint:', '  parallelism: bad\n  entrypoint:')


def codes(ds):
    return [d['code'] for d in ds]


def ordinary():
    # Invalid Workflows configuration is a deterministic acquisition trap: any
    # accidental workspace load produces WF_ANALYSIS_UNAVAILABLE on old code.
    dest, work = m.setup('ordinary', {}, m.CONFIG.replace('3.6.0', '99.99.99'))
    c = m.LSP(dest, work)
    try:
        for name, text in FOREIGN.items():
            ds = c.open(work / (name + '.yaml'), text)
            m.result(name, {'no_workflows_diagnostics': ds == []}, {'diagnostics': ds})
    finally:
        c.close()
    m.result('no-workspace-acquisition', {
        'no_workspace_error_log': b'workspace incomplete:' not in c.stderr_raw,
    })


def owned():
    kinds = ('Workflow', 'WorkflowTemplate', 'ClusterWorkflowTemplate', 'CronWorkflow',
             'WorkflowEventBinding', 'WorkflowArtifactGCTask', 'WorkflowTaskResult', 'WorkflowTaskSet')
    examples = {kind: 'apiVersion: argoproj.io/v1alpha1\nkind: ' + kind + '\nmetadata: {name: [invalid]}\n'
                for kind in kinds}
    # TaskResult has no spec and its upstream metadata schema is permissive.
    examples['WorkflowTaskResult'] += 'outputs: {result: true}\n'
    examples['quoted-flow'] = '{"kind": "Workflow", "apiVersion": "argoproj.io/v1alpha1", metadata: {name: [invalid]}}\n'
    examples['merged-identity'] = ('identity: &identity {apiVersion: argoproj.io/v1alpha1, kind: Workflow}\n'
                                   '<<: *identity\nmetadata: {name: [invalid]}\n')
    examples['alias-identity'] = ('api: &api argoproj.io/v1alpha1\ntype: &type Workflow\n'
                                  'apiVersion: *api\nkind: *type\nmetadata: {name: [invalid]}\n')
    examples['mixed-stream'] = FOREIGN['deployment'] + '---\n' + examples['Workflow']
    for name, text in examples.items():
        dest, work = m.setup('owned-' + name, {'case.yaml': text}, 'schemaVersion: 1\n')
        c = m.LSP(dest, work)
        try:
            ds = c.open(work / 'case.yaml', text)
            m.result('owned-' + name, {'schema_error_preserved': any(
                                         d['code'] in ('SCHEMA_TYPE', 'SCHEMA_VALIDATION') and d['severity'] == 1 for d in ds),
                                     'analysis_completed': 'WF_ANALYSIS_INCOMPLETE' not in codes(ds)},
                     {'diagnostics': ds})
        finally:
            c.close()


def transitions():
    dest, work = m.setup('transitions', {'case.yaml': BROKEN})
    c = m.LSP(dest, work)
    file = work / 'case.yaml'
    try:
        before = c.open(file, BROKEN)
        plain = c.change(file, FOREIGN['deployment'], 2)
        restored = c.change(file, BROKEN, 3)
        repaired = c.change(file, m.REN_DAG, 4)
        m.result('workflow-ordinary-workflow', {
            'initial_schema_error': codes(before) == ['SCHEMA_TYPE'],
            'stale_diagnostics_cleared': plain == [],
            'unsaved_workflow_reactivated': codes(restored) == ['SCHEMA_TYPE'],
            'repair_clears_error': repaired == [],
            'disk_unchanged': file.read_bytes() == BROKEN.encode(),
        })
    finally:
        c.close()


def deadline():
    # Invoked only by the separately recorded delayed-analysis control build.
    # Keep the actual five-second server deadline and normal wire protocol.
    dest, work = m.setup('deadline', {'a-workflow.yaml': BROKEN})
    c = m.LSP(dest, work)
    workflow = work / 'a-workflow.yaml'
    foreign = [work / 'z-ordinary.yaml']
    try:
        folders = []
        for i in range(1, 6):
            root = work.parent / ('repo-' + str(i))
            root.mkdir()
            folders.append({'uri': root.as_uri(), 'name': root.name})
            foreign.append(root / 'z-ordinary.yaml')
        c.send('workspace/didChangeWorkspaceFolders', {'event': {'added': folders, 'removed': []}})
        started = time.monotonic()
        for file, text in [(workflow, BROKEN)] + [(p, FOREIGN['deployment']) for p in foreign]:
            c.send('textDocument/didOpen', {'textDocument': {
                'uri': file.as_uri(), 'languageId': 'yaml', 'version': 1, 'text': text}})
        # The foreign files sort AFTER the Workflow, so a one-pass guard still
        # lets the first file's deadline poison them. Collect every publication.
        until = started + 12
        expected = {p.as_uri() for p in [workflow] + foreign}
        while time.monotonic() < until:
            publications = [x['params'] for x in c.received if x.get('method') == 'textDocument/publishDiagnostics']
            if expected <= {p['uri'] for p in publications}:
                break
            if c.p.poll() is not None:
                raise RuntimeError('server exited during deadline control')
            time.sleep(.02)
        workflow_ds = [p['diagnostics'] for p in publications if p['uri'] == workflow.as_uri()]
        foreign_events = [p for p in publications if p['uri'] != workflow.as_uri()]
        m.result('deadline-isolation', {
            'all_six_roots_published': expected == {p['uri'] for p in publications},
            'workflow_timeout_visible': len(workflow_ds) == 1 and codes(workflow_ds[0]) == ['WF_ANALYSIS_INCOMPLETE'],
            'ordinary_files_clear': len(foreign_events) == 6 and all(p['diagnostics'] == [] for p in foreign_events),
            'foreign_cleared_before_timeout': len(publications) == 7 and publications[-1]['uri'] == workflow.as_uri(),
            'current_versions': all(p.get('version') == 1 for p in publications),
            'no_false_input_blame': len(workflow_ds) == 1 and all('Correct the input' not in d['message']
                                                                             for d in workflow_ds[0]),
        }, {'publications': publications, 'seconds': time.monotonic() - started})
        # A former Workflow must clear even if it would sort after a slow file.
        cleared = c.change(workflow, FOREIGN['plain'], 2)
        m.result('deadline-edit-recovery', {'stale_timeout_cleared': cleared == []})
    finally:
        c.close()


try:
    if '--deadline' in sys.argv:
        deadline()
        expected = 2
    else:
        ordinary()
        owned()
        transitions()
        expected = len(FOREIGN) + 1 + 12 + 1
except Exception as error:
    expected = -1
    m.result('runner', {'completed': False}, {'error': repr(error), 'traceback': traceback.format_exc()})
finally:
    m.save(m.OUT / 'manifest.json', {
        'command': sys.argv, 'binarySha256': m.sha(m.BINARY),
        'testSha256': m.sha(pathlib.Path(__file__)), 'expectedCases': expected,
        'deadlineControl': '--deadline' in sys.argv,
    })
sys.exit(0 if len(m.RESULTS) == expected and all(row['pass'] for row in m.RESULTS) else 1)
