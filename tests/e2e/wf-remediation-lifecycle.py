#!/usr/bin/env python3
"""Publication, cancellation and changed-version recovery on a live LSP process."""
import importlib.util, pathlib, sys, traceback

p = pathlib.Path(__file__).with_name('wf-audit-gaps.py')
spec = importlib.util.spec_from_file_location('wire', p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
text = m.REN_DAG
broken = text.replace('  entrypoint:', '  parallelism: bad\n  entrypoint:')
dest, work = m.setup('lifecycle', {'a.yaml': text, 'b.yaml': broken})
c = m.LSP(dest, work)
a, b = work / 'a.yaml', work / 'b.yaml'
try:
    c.open(a, text)
    before_b = c.open(b, broken)
    # A cancellation must produce the protocol's cancellation error, once.
    c.next_id += 1
    ident = c.next_id
    c.send('textDocument/references', {'textDocument': {'uri': a.as_uri()}, 'position': m.pos(text, 'name: work', offset=7)}, ident)
    c.send('$/cancelRequest', {'id': ident})
    response = c.wait(lambda x: x.get('id') == ident)
    ds = c.change(a, broken, 2)
    m.result('cancel-recovery', {
        'cancelled': response.get('error', {}).get('code') == -32800 and 'result' not in response,
        'one_terminal': sum(x.get('id') == ident for x in c.received) == 1,
        'current_type_error': [x['code'] for x in ds] == ['SCHEMA_TYPE'],
    }, {'response': response})
    for version in range(3, 20):
        c.send('textDocument/didChange', {'textDocument': {'uri': a.as_uri(), 'version': version}, 'contentChanges': [{'text': broken + '#' + str(version)}]})
    ds = c.diagnostics(a.as_uri(), 19)
    m.result('latest-version', {'type_error': [x['code'] for x in ds] == ['SCHEMA_TYPE']})
    # Start with a nonempty publication and observe a NEW clearing event. An
    # initial empty diagnostic list must not satisfy workspace removal.
    marker = len(c.received)
    c.send('textDocument/didClose', {'textDocument': {'uri': a.as_uri()}})
    c.send('workspace/didChangeWorkspaceFolders', {'event': {'added': [], 'removed': [{'uri': work.as_uri(), 'name': 'root'}]}})
    cleared = c.wait(lambda x: x.get('method') == 'textDocument/publishDiagnostics' and x['params']['uri'] == b.as_uri() and x['params']['diagnostics'] == [])
    m.result('remove-root', {
        'previous_type_error': [x['code'] for x in before_b] == ['SCHEMA_TYPE'],
        'new_clear_publication': any(x is cleared for x in c.received[marker:]),
    }, {'publication': cleared})
except Exception as error:
    m.result('runner', {'completed': False}, {'error': repr(error), 'traceback': traceback.format_exc()})
finally:
    c.close()
    m.save(m.OUT / 'manifest.json', {'command': sys.argv, 'binarySha256': m.sha(m.BINARY), 'testSha256': m.sha(pathlib.Path(__file__)), 'inputs': {str(p.relative_to(work)): m.sha(p) for p in work.rglob('*') if p.is_file()}})
sys.exit(0 if len(m.RESULTS) == 3 and all(x['pass'] for x in m.RESULTS) else 1)
