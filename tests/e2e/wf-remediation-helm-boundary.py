#!/usr/bin/env python3
"""Manifest-driven YAML/Helm stdio coverage; real Zed attachment is separate."""
import importlib.util
import pathlib
import sys
import traceback

p = pathlib.Path(__file__).with_name('wf-audit-gaps.py')
spec = importlib.util.spec_from_file_location('wire', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
inputs = {}


def manifest_languages():
    # Editor registration is checked by the standalone adapter package suite.
    # This consumer exercises the server's editor-independent protocol IDs.
    return ['yaml', 'helm']


def hover(client, path, text):
    return client.request('textDocument/hover', {
        'textDocument': {'uri': path.as_uri()},
        'position': m.pos(text, 'entrypoint', offset=2),
    })


def check_state(name, path, disk_bytes, diagnostics, response, owned, broken=False):
    checks = {'source_unchanged': path.read_bytes() == disk_bytes}
    if not owned:
        checks['chart_source_excluded'] = diagnostics == []
        error = response.get('error', {})
        checks['hover_refuses_chart_source'] = ('result' not in response and error.get('code') == -32803
                                               and error.get('message') == 'Helm template source is excluded')
    else:
        checks['hover_ownership'] = 'error' not in response and bool(response.get('result'))
        if broken:
            checks['current_schema_error'] = any(d['code'] == 'SCHEMA_TYPE' and d['severity'] == 1
                                                 for d in diagnostics)
        else:
            checks['no_schema_error'] = not any(d['code'] == 'SCHEMA_TYPE' for d in diagnostics)
    m.result(name, checks, {'diagnostics': diagnostics, 'hover': response})


def language_case(language_id, next_language_id):
    # Argo expressions are not a reason to exclude ordinary YAML. Actual chart
    # templates remain excluded even when their contents contain no Helm syntax.
    text = m.REN_DAG.replace('args: [work]', 'args: ["{{workflow.name}}"]')
    broken = text.replace('  entrypoint:', '  parallelism: bad\n  entrypoint:')
    template = text.replace('entrypoint: main', 'entrypoint: {{ .Values.entrypoint }}')
    dest, work = m.setup('helm-boundary-' + language_id, {
        'chart/Chart.yaml': 'apiVersion: v2\nname: sample\nversion: 0.1.0\n',
        'chart/templates/workflow.yaml': text,
        'templates/plain.yaml': text,
        'ordinary.yaml': text,
    })
    inputs.update({str(path.relative_to(m.OUT)): m.sha(path)
                   for path in work.rglob('*') if path.is_file()})
    client = m.LSP(dest, work)
    try:
        for relative, owned in [('chart/templates/workflow.yaml', False),
                                ('templates/plain.yaml', True), ('ordinary.yaml', True)]:
            path = work / relative
            disk_bytes = path.read_bytes()
            name = language_id + '/' + relative
            diagnostics = client.open(path, text, language_id=language_id)
            client.send('textDocument/didSave', {'textDocument': {'uri': path.as_uri()}})
            check_state(name + '/open', path, disk_bytes, diagnostics,
                        hover(client, path, text), owned)

            diagnostics = client.change(path, broken, 2)
            check_state(name + '/unsaved-error', path, disk_bytes, diagnostics,
                        hover(client, path, broken), owned, broken=True)
            diagnostics = client.change(path, text, 3)
            check_state(name + '/unsaved-recovery', path, disk_bytes, diagnostics,
                        hover(client, path, text), owned)

            if not owned:
                diagnostics = client.change(path, template, 4)
                check_state(name + '/helm-expression', path, disk_bytes, diagnostics,
                            hover(client, path, template), owned)

            # Language changes are close/open lifetimes, not didChange fields.
            # Observe the unversioned close publication before resetting to 1,
            # so an old version-1 message cannot satisfy the reopening check.
            client.send('textDocument/didClose', {'textDocument': {'uri': path.as_uri()}})
            client.wait(lambda message: message.get('method') == 'textDocument/publishDiagnostics'
                        and message.get('params', {}).get('uri') == path.as_uri()
                        and message['params'].get('version') is None)
            diagnostics = client.open(path, broken, version=1, language_id=next_language_id)
            check_state(name + '/reopen-' + next_language_id, path, disk_bytes, diagnostics,
                        hover(client, path, broken), owned, broken=True)
    finally:
        client.close()


try:
    language_ids = manifest_languages()
    for index, language_id in enumerate(language_ids):
        language_case(language_id, language_ids[1 - index])
except Exception as error:
    m.result('runner', {'completed': False}, {'error': repr(error), 'traceback': traceback.format_exc()})
finally:
    m.save(m.OUT / 'manifest.json', {
        'command': sys.argv,
        'binarySha256': m.sha(m.BINARY),
        'testSha256': m.sha(pathlib.Path(__file__)),
        'clientSha256': m.sha(p),
        'inputs': inputs,
    })
sys.exit(0 if len(m.RESULTS) == 26 and all(row['pass'] for row in m.RESULTS) else 1)
