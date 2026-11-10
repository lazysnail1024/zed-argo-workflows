#!/usr/bin/env python3
"""A local publication during a real reconciliation must not acknowledge external work."""
import importlib.util
import json
import pathlib
import sys
import time
import traceback

spec = importlib.util.spec_from_file_location('wire', pathlib.Path(__file__).with_name('wf-audit-gaps.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
text = m.CALLER.replace('  entrypoint:', '  paralelism: 1\n  entrypoint:')
dest, work = m.setup('reconciliation-publication', {'caller.yaml': text})
background = work / 'background'
background.mkdir()
for n in range(10000):
    (background / ('%05d.yaml' % n)).write_text('apiVersion: v1\nkind: ConfigMap\nmetadata: {name: ordinary}\n')
client = None
checks, observed = {}, {}


def states():
    rows = []
    for line in bytes(client.stderr_raw).splitlines():
        try:
            row = json.loads(line)
            if row.get('phase') == 'index' and row.get('root') == str(work):
                rows.append(row)
        except (ValueError, UnicodeError):
            pass
    return rows


def wait(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.p.poll() is not None:
            raise RuntimeError('server exited during publication observation')
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    return None


def publications(version):
    return [row['params'] for row in client.received if row.get('method') == 'textDocument/publishDiagnostics'
            and row['params']['uri'] == (work / 'caller.yaml').as_uri() and row['params'].get('version') == version]


def missing(row):
    return any(item['code'] == 'WF_TEMPLATE_REF_UNRESOLVED' for item in row['diagnostics'])


try:
    client = m.LSP(dest, work)
    client.open(work / 'caller.yaml', text)
    first = wait(lambda: next((row for row in states() if row['state'] == 'Ready'), None), 30)
    checks['first_complete_pass'] = first is not None and first['files'] == 10001
    initial = wait(lambda: next((row for row in publications(1) if missing(row)), None), 3)
    checks['initial_external_diagnostic'] = initial is not None
    second = wait(lambda: next((row for row in states() if row['pass'] >= 2 and row['state'] in ('Discovering', 'Indexing')), None), 45)
    checks['natural_reconciliation_started'] = second is not None
    client.send('textDocument/didChange', {'textDocument': {'uri': (work / 'caller.yaml').as_uri(), 'version': 2},
                'contentChanges': [{'text': text + '# current overlay\n'}]})
    local = wait(lambda: next(iter(publications(2)), None), 3)
    checks['local_publication_observed_before_complete_pass'] = local is not None and not missing(local)
    ready = wait(lambda: next((row for row in states() if row['pass'] >= 2 and row['state'] == 'Ready'), None), 30)
    checks['second_pass_completed'] = ready is not None and ready['files'] == 10001
    repaired = wait(lambda: next((row for row in publications(2) if missing(row)), None), 3)
    checks['ready_republishes_unacknowledged_external_diagnostic'] = repaired is not None
    observed.update(first=first, second=second, local=local, ready=ready, repaired=repaired)
except Exception:
    checks['completed'] = False
    observed['exception'] = traceback.format_exc()
finally:
    if client is not None:
        try:
            client.close()
            checks['clean_shutdown'] = True
        except Exception:
            checks['clean_shutdown'] = False
    m.result('reconciliation-publication', checks, observed)
    m.save(m.OUT / 'manifest.json', {'command': sys.argv, 'binarySha256': m.sha(m.BINARY),
        'testSha256': m.sha(pathlib.Path(__file__)), 'helperSha256': m.sha(pathlib.Path(__file__).with_name('wf-audit-gaps.py')),
        'watcherNotifications': 0, 'timerSeconds': 30, 'backgroundFiles': 10000})
raise SystemExit(0 if all(row['pass'] for row in m.RESULTS) else 1)
