#!/usr/bin/env python3
"""Independent stdio checks for mixed roots and live template locations."""
import argparse
import importlib.util
import json
import pathlib
import shutil
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('binary', type=pathlib.Path)
parser.add_argument('output', type=pathlib.Path)
parser.add_argument('--ordinary-files', type=int, default=2048)
parser.add_argument('--padding-bytes', type=int, default=0)
args = parser.parse_args()
spec = importlib.util.spec_from_file_location('wire', pathlib.Path(__file__).with_name('wf-audit-gaps.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.BINARY = args.binary.resolve()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=True)
root = out / 'input'
if root.exists():
    shutil.rmtree(root)
root.mkdir()
(root / '.argo-workflows-ls.yaml').write_bytes(m.CONFIG.encode())
foreign = 'apiVersion: argoproj.io/v1alpha1\nkind: Application\nmetadata: {name: other}\nspec: {project: absent}\n'
for i in range(args.ordinary_files):
    text = foreign if i % 100 == 0 else 'apiVersion: v1\nkind: ConfigMap\nmetadata: {name: ordinary-' + str(i) + '}\n'
    if args.padding_bytes:
        text += '# ' + 'x' * args.padding_bytes + '\n'
    file = root / f'a-background/repo-{i % 8:02d}/file-{i:05d}.yaml'
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(text.encode())
fixtures = pathlib.Path(__file__).resolve().parents[1] / 'fixtures' / 'external'
consumer_text = (fixtures / 'consumer.yaml').read_text() + '---\n' + foreign
target_text = (fixtures / 'external.yaml').read_text()
related = root / 'z-related'
related.mkdir()
consumer, target, other = related / 'consumer.yaml', related / 'template.yaml', related / 'application.yaml'
consumer.write_bytes(consumer_text.encode())
target.write_bytes(target_text.encode())
other.write_bytes(foreign.encode())
checks, observed = {}, {}
client = None


def position(text, needle):
    offset = text.index(needle)
    before = text[:offset]
    return {'line': before.count('\n'), 'character': len(before.rsplit('\n', 1)[-1])}


def definition():
    response = client.request('textDocument/definition', {
        'textDocument': {'uri': consumer.as_uri()},
        'position': position(consumer_text, 'run'),
    })
    if response.get('error', {}).get('code') == -32801:
        return None  # ContentModified is safe while a fresh result is pending.
    assert 'error' not in response, response
    return response.get('result') or []


def await_definition(expected, timeout=10):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = definition()
        if last is not None and expected(last):
            return last
        time.sleep(.02)
    raise AssertionError({'definitionDeadline': timeout, 'last': last})


def at_target(locations, text=target_text):
    return len(locations) == 1 and locations[0]['uri'] == target.as_uri() and locations[0]['range']['start'] == position(text, 'run')


def changed(path, kind):
    client.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': path.as_uri(), 'type': kind}]})


try:
    started = time.monotonic()
    client = m.LSP(out, root)
    observed['initializeSeconds'] = time.monotonic() - started
    began = time.monotonic()
    observed['firstDiagnostics'] = client.open(consumer, consumer_text)
    observed['firstPublicationSeconds'] = time.monotonic() - began
    observed['startupToFirstPublicationSeconds'] = time.monotonic() - started
    checks['current_file_publication_under_two_seconds'] = observed['firstPublicationSeconds'] < 2
    observed['initialDefinition'] = await_definition(at_target)
    checks['mixed_document_cross_file_definition'] = True
    checks['foreign_resource_has_no_workflows_diagnostics'] = client.open(other, foreign) == []

    client.open(target, target_text.replace('name: run', 'name: different'))
    await_definition(lambda locations: locations == [])
    target.write_bytes(target_text.encode())
    changed(target, 2)
    checks['open_target_overrides_disk_watch'] = definition() in (None, [])
    client.send('textDocument/didClose', {'textDocument': {'uri': target.as_uri()}})
    await_definition(at_target)
    checks['close_restores_current_disk_target'] = True

    shifted = '# location changed without changing the template\n\n' + target_text
    target.write_bytes(shifted.encode())
    changed(target, 2)
    immediate = definition()
    checks['watch_does_not_return_old_position'] = not immediate or at_target(immediate, shifted)
    observed['shiftedDefinition'] = await_definition(lambda locations: at_target(locations, shifted))
    checks['position_only_change_refreshes_definition'] = True

    target.unlink()
    changed(target, 3)
    checks['delete_does_not_return_old_target'] = definition() in (None, [])
    await_definition(lambda locations: locations == [])
    checks['deleted_target_is_withdrawn'] = True
    target.write_bytes(target_text.encode())
    changed(target, 1)
    await_definition(at_target)
    checks['recreated_target_resolves'] = True
except Exception as exc:
    checks['exception'] = repr(exc)
finally:
    if client is not None:
        try:
            client.close()
            checks['clean_shutdown'] = True
        except Exception as exc:
            checks['shutdownException'] = repr(exc)
    report = {'command': __import__('sys').argv,
              'binarySHA256': m.sha(args.binary),
              'testSHA256': m.sha(pathlib.Path(__file__)),
              'ordinaryFiles': args.ordinary_files,
              'paddingBytesPerFile': args.padding_bytes,
              'checks': checks, 'observed': observed,
              'pass': bool(checks) and all(value is True for value in checks.values()),
              'scope': 'Native stdio behavior; this is not the full 50k or Zed GUI acceptance.'}
    (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
raise SystemExit(0 if report['pass'] else 1)
