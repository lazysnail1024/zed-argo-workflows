#!/usr/bin/env python3
"""Independent black-box CLI/LSP audit. No production imports or expected-value derivation."""
import hashlib, json, os, pathlib, queue, subprocess, sys, threading, time, traceback

HERE = pathlib.Path(__file__).resolve().parent
BINARY = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE / 'argo-workflows-ls').resolve()
OUT = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else HERE.parent.parent / 'artifacts/wf-audit-gaps').resolve()
ARGO = pathlib.Path(os.environ.get('WF_TEST_ARGO_BINARY', '/tmp/zed-argo-official-3.6.0/argo')).resolve()
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
CONFIG = 'schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\n'

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, obj): path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n')
def setup(name, files, config=CONFIG):
    dest = OUT / name
    work = dest / 'input'
    work.mkdir(parents=True, exist_ok=True)
    # Preserve intentional LF/CRLF bytes on every host, including Windows.
    (work / '.argo-workflows-ls.yaml').write_bytes(config.encode('utf-8'))
    for name, value in files.items():
        p = work / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(value.encode('utf-8'))
    return dest, work
def command(dest, label, argv, timeout=20, **kwargs):
    argv = [str(a) for a in argv]
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as error:
        # Timeout is a failure, but its partial output is often the only evidence
        # of the blocked protocol or subprocess operation.
        (dest / (label + '.stdout')).write_bytes(error.stdout or b'')
        (dest / (label + '.stderr')).write_bytes(error.stderr or b'')
        save(dest / (label + '.command.json'), {'argv': argv, 'exitCode': None, 'timedOut': True, 'timeoutSeconds': timeout})
        raise
    (dest / (label + '.stdout')).write_bytes(r.stdout)
    (dest / (label + '.stderr')).write_bytes(r.stderr)
    save(dest / (label + '.command.json'), {'argv': argv, 'exitCode': r.returncode, 'timedOut': False})
    return r
def cli(dest, work, label='check', only=None):
    argv = [BINARY, 'check', '--root', work, '--format', 'json']
    if only: argv += ['--file', work / only]
    r = command(dest, label, argv)
    return r.returncode, json.loads(r.stdout)
def official(dest, path, label):
    config = dest / 'empty-kubeconfig'
    config.write_bytes('apiVersion: v1\nkind: Config\nclusters: []\ncontexts: []\nusers: []\n'.encode('utf-8'))
    return command(dest, label, [ARGO, 'lint', '--offline', '--no-color', '--output', 'simple', '--kubeconfig', config, path], env={'PATH': '/usr/bin:/bin', 'KUBECONFIG': str(config)}).returncode
def wf(ds): return [(d['code'], d['severity']) for d in ds if d['code'].startswith('WF_')]
def pos(text, needle, occurrence=0, offset=0):
    start = -1
    for _ in range(occurrence+1): start = text.index(needle, start+1)
    start += offset
    head = text[:start]
    return {'line': head.count('\n'), 'character': len(head.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}
def apply(text, result, uri, version):
    changes = result['documentChanges']
    assert len(changes) == 1 and changes[0]['textDocument'] == {'uri': uri, 'version': version}
    lines = text.splitlines(keepends=True)
    def off(p):
        tail = lines[p['line']].encode('utf-16-le')[:p['character']*2].decode('utf-16-le')
        return sum(len(line) for line in lines[:p['line']]) + len(tail)
    edits = [(off(e['range']['start']), off(e['range']['end']), e['newText']) for e in changes[0]['edits']]
    for a, b, value in sorted(edits, reverse=True): text = text[:a] + value + text[b:]
    return text
def result(name, assertions, details=None):
    v = {'case': name, 'assertions': assertions, 'pass': bool(assertions) and all(v is True for v in assertions.values()), 'details': details or {}}
    RESULTS.append(v)
    save(OUT / 'results.json', RESULTS)
    print(name, 'PASS' if v['pass'] else 'FAIL', flush=True)

class LSP:
    def __init__(self, dest, work):
        self.dest, self.work = dest, work
        self.raw, self.sent, self.received, self.q = bytearray(), [], [], queue.Queue()
        self.received_times = []
        self.next_id = 0
        self.p = subprocess.Popen([str(BINARY), 'serve', '--stdio'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.stderr_raw = bytearray()
        def read_stderr():
            while True:
                data = self.p.stderr.read(4096)
                if not data: return
                self.stderr_raw.extend(data)
        self.stderr_reader = threading.Thread(target=read_stderr, daemon=True)
        self.stderr_reader.start()
        def read():
            try:
                while True:
                    headers = {}
                    while True:
                        line = self.p.stdout.readline()
                        self.raw.extend(line)
                        if not line: raise EOFError('server stdout closed')
                        if line == b'\r\n': break
                        key, value = line.decode().strip().split(':', 1)
                        headers[key.lower()] = value.strip()
                    data = self.p.stdout.read(int(headers['content-length']))
                    self.raw.extend(data)
                    value = json.loads(data)
                    self.received_times.append(time.monotonic())
                    self.received.append(value)
                    self.q.put(value)
            except Exception as e: self.q.put(e)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            self.init = self.request('initialize', {'rootUri': work.as_uri(), 'capabilities': {'workspace': {'workspaceEdit': {'documentChanges': True}}}})
            assert self.init.get('jsonrpc') == '2.0' and isinstance(self.init.get('result', {}).get('capabilities'), dict), self.init
            self.send('initialized', {})
        except BaseException:
            if self.p.poll() is None: self.p.kill(); self.p.wait()
            self.save_evidence()
            raise
    def send(self, method, params=None, ident=None):
        m = {'jsonrpc': '2.0', 'method': method}
        if params is not None: m['params'] = params
        if ident is not None: m['id'] = ident
        self.sent.append(m)
        payload = json.dumps(m).encode()
        self.p.stdin.write(('Content-Length: %d\r\n\r\n' % len(payload)).encode() + payload)
        self.p.stdin.flush()
    def wait(self, predicate):
        deadline = time.monotonic() + 20
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError('LSP response deadline exceeded')
            value = self.q.get(timeout=remaining)
            if isinstance(value, Exception): raise value
            if predicate(value): return value
    def request(self, method, params=None):
        self.next_id += 1
        self.send(method, params, self.next_id)
        return self.wait(lambda x: x.get('id') == self.next_id)
    def diagnostics(self, uri, version):
        return self.wait(lambda x: x.get('method') == 'textDocument/publishDiagnostics' and x['params']['uri'] == uri and x['params'].get('version') == version)['params']['diagnostics']
    def open(self, path, text, version=1):
        self.send('textDocument/didOpen', {'textDocument': {'uri': path.as_uri(), 'version': version, 'languageId': 'yaml', 'text': text}})
        return self.diagnostics(path.as_uri(), version)
    def change(self, path, text, version):
        self.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': version}, 'contentChanges': [{'text': text}]})
        return self.diagnostics(path.as_uri(), version)
    def close(self):
        try:
            if self.p.poll() is None:
                response = self.request('shutdown')
                assert 'result' in response and response['result'] is None and 'error' not in response, response
                self.send('exit')
                self.p.wait(timeout=5)
            assert self.p.returncode == 0, 'LSP did not exit cleanly: %r' % self.p.returncode
        finally:
            if self.p.poll() is None: self.p.kill(); self.p.wait()
            self.save_evidence()
    def save_evidence(self):
        self.reader.join(timeout=1)
        (self.dest / 'lsp.stdout.raw').write_bytes(self.raw)
        self.stderr_reader.join(timeout=1)
        (self.dest / 'lsp.stderr.raw').write_bytes(self.stderr_raw)
        save(self.dest / 'traffic.json', {'sent': self.sent, 'received': self.received, 'receivedMonotonic': self.received_times})
        save(self.dest / 'lsp.command.json', {'argv': [str(BINARY), 'serve', '--stdio'], 'exitCode': self.p.returncode})

REN_DAG = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: rename-case, namespace: argo}
spec:
  entrypoint: main
  templates:
    - name: main
      dag:
        tasks:
          - name: run
            template: work
            onExit: cleanup
    - name: work
      container: {image: alpine, command: [echo], args: [work]}
    - name: cleanup
      container: {image: alpine, command: [echo], args: [cleanup]}
'''
BASE = '''apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata: {name: base, namespace: argo}
spec:
  entrypoint: work
  templates:
    - name: work
      container: {image: alpine}
'''
CALLER = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: caller, namespace: argo}
spec:
  entrypoint: main
  templates:
    - name: main
      dag:
        tasks:
          - name: run
            templateRef: {name: base, template: work}
'''

def run_rename(name, text):
    dest, work = setup(name, {'case.yaml': text})
    file = work / 'case.yaml'; uri = file.as_uri()
    before = official(dest, file, 'official-before')
    lsp = LSP(dest, work)
    try:
        ds = lsp.open(file, text)
        position = pos(text, 'name: cleanup', offset=7)
        response = lsp.request('textDocument/rename', {'textDocument': {'uri': uri}, 'position': position, 'newName': 'tidy'})
        if 'error' in response:
            result(name, {'baseline_official_valid': before == 0, 'safe_refusal': response['error'].get('code') == -32602 and bool(response['error'].get('message')) and 'result' not in response}, response)
        else:
            changed = apply(text, response['result'], uri, 1)
            after_path = dest / 'after.yaml'; after_path.write_bytes(changed.encode('utf-8'))
            after = official(dest, after_path, 'official-after')
            after_ds = lsp.change(file, changed, 2)
            expected = text.replace('onExit: cleanup', 'onExit: tidy').replace('name: cleanup', 'name: tidy')
            result(name, {'baseline_official_valid': before == 0, 'all_static_references_updated': changed == expected, 'official_remains_valid': after == 0, 'disk_unchanged': file.read_text() == text}, {'beforeDiagnostics': ds, 'afterDiagnostics': after_ds, 'response': response, 'officialAfterExit': after})
    finally: lsp.close()

def run_fix(incomplete=False):
    name = 'incomplete-scope-field-fix' if incomplete else 'fieldfix-external'
    text = CALLER.replace('template: work}', 'template: work, clusterScop: true}')
    files = {'case.yaml': text, 'base.yaml': BASE}
    if incomplete: files['broken.yaml'] = 'apiVersion: [\n'
    dest, work = setup(name, files)
    before_code, before = cli(dest, work, 'check-before', 'case.yaml')
    file = work / 'case.yaml'; uri = file.as_uri(); lsp = LSP(dest, work)
    try:
        ds = lsp.open(file, text)
        p = pos(text, 'clusterScop')
        response = lsp.request('textDocument/codeAction', {'textDocument': {'uri': uri}, 'range': {'start': p, 'end': p}, 'context': {'diagnostics': ds}})
        choices = [a for a in response.get('result', []) if 'clusterScope' in a['title']]
        if incomplete:
            result(name, {'semantic_fix_withheld_while_scope_incomplete': isinstance(response.get('result'), list) and not choices and 'error' not in response}, {'beforeDiagnostics': ds, 'actionResponse': response})
        elif not choices:
            result(name, {'unsafe_fix_withheld': isinstance(response.get('result'), list) and 'error' not in response, 'baseline_has_no_external_error': not any(c == 'WF_TEMPLATE_REF_UNRESOLVED' and s == 'Error' for c, s in wf(before['diagnostics']))}, response)
        else:
            resolved = lsp.request('codeAction/resolve', choices[0])
            changed = apply(text, resolved['result']['edit'], uri, 1)
            (dest / 'after.yaml').write_bytes(changed.encode('utf-8'))
            ds_after = lsp.change(file, changed, 2)
            result(name, {'baseline_has_no_external_error': not any(c == 'WF_TEMPLATE_REF_UNRESOLVED' and s == 'Error' for c, s in wf(before['diagnostics'])), 'exact_fix_bytes': changed == text.replace('clusterScop:', 'clusterScope:'), 'fix_adds_no_error': not any(d['severity'] == 1 for d in ds_after)}, {'beforeDiagnostics': ds, 'afterDiagnostics': ds_after, 'resolved': resolved})
    finally: lsp.close()

def run_scopes():
    broken = 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: broken}\nspec: [\n'
    duplicate = BASE.replace('name: base, namespace: argo', 'name: base, name: shadow, namespace: argo')
    specs = [
        ('scope-separate-damage', {'alpha/caller.yaml': CALLER, 'beta/broken.yaml': broken}, CONFIG + 'scopes:\n  - name: alpha\n    include: ["alpha/**"]\n  - name: beta\n    include: ["beta/**"]\n', 'Error'),
        ('scope-same-damage', {'caller.yaml': CALLER, 'broken.yaml': broken}, CONFIG, 'Information'),
        ('scope-same-duplicate', {'caller.yaml': CALLER.replace('name: base, template:', 'name: absent, template:'), 'broken.yaml': duplicate}, CONFIG, 'Information'),
        ('unknown-namespace', {'caller.yaml': CALLER.replace(', namespace: argo', '')}, CONFIG, 'Information'),
    ]
    for name, files, config, severity in specs:
        dest, work = setup(name, files, config)
        code, data = cli(dest, work)
        actual = [d['severity'] for d in data['diagnostics'] if d['code'] == 'WF_TEMPLATE_REF_UNRESOLVED']
        result(name, {'completion_exit': code == (0 if name=='unknown-namespace' else 2) and data['analysisComplete'] is (name=='unknown-namespace'), 'missing_reference_severity': actual == [severity]}, {'actual': actual, 'expected': [severity], 'notEvaluated': data['notEvaluated']})

def run_unsupported():
    name = 'unsupported-gvk'
    text = BASE.replace('argoproj.io/v1alpha1', 'argoproj.io/v999')
    dest, work = setup(name, {'caller.yaml': CALLER, 'base.yaml': text})
    code, data = cli(dest, work)
    lsp = LSP(dest, work)
    try:
        lsp.open(work/'caller.yaml', CALLER)
        symbols = lsp.request('textDocument/documentSymbol', {'textDocument': {'uri': (work/'base.yaml').as_uri()}})
        definition = lsp.request('textDocument/definition', {'textDocument': {'uri': (work/'caller.yaml').as_uri()}, 'position': pos(CALLER, 'name: base', offset=7)})
        result(name, {'unsupported_resource_not_a_definition': definition.get('result') == [], 'unsupported_resource_not_symbols': symbols.get('result') == [], 'caller_not_falsely_resolved': any(d['code']=='WF_TEMPLATE_REF_UNRESOLVED' for d in data['diagnostics'])}, {'symbols': symbols, 'definition': definition, 'cli': data})
    finally: lsp.close()

def run_alias():
    name = 'alias-input'
    text = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: alias-case}
spec:
  entrypoint: main
  arguments:
    parameters: [{name: message, value: hello}]
  templates:
    - name: helper
      inputs: &sharedInputs
        parameters: [{name: message}]
      container: {image: alpine}
    - name: main
      inputs: *sharedInputs
      container:
        image: alpine
        command: [echo]
        args: ["{{inputs.parameters.message}}"]
'''
    dest, work = setup(name, {'case.yaml': text})
    official_code = official(dest, work/'case.yaml', 'official')
    code, data = cli(dest, work)
    result(name, {'official_valid': official_code == 0, 'no_false_input_scope_error': not any(d['code']=='WF_REFERENCE_SCOPE' for d in data['diagnostics'])}, {'diagnostics': data['diagnostics'], 'notEvaluated': data['notEvaluated']})

def run_workspace_symbols():
    name = 'workspace-symbol-baseline'
    one = BASE.replace('work', 'old-disk')
    changed = BASE.replace('work', 'changed-buffer')
    fresh = BASE.replace('work', 'unsaved-buffer')
    excluded = BASE.replace('work', 'excluded-buffer')
    dest, work = setup(name, {'case.yaml': one, 'vendor/excluded.yaml': excluded})
    lsp = LSP(dest, work)
    try:
        lsp.open(work/'case.yaml', changed)
        lsp.open(work/'new.yaml', fresh)
        lsp.open(work/'vendor/excluded.yaml', excluded)
        response = lsp.request('workspace/symbol', {'query': ''})
        actual = sorted((s['name'], s['location']['uri'].removeprefix(work.as_uri())) for s in response.get('result', []))
        expected = [('changed-buffer','/case.yaml'), ('excluded-buffer','/vendor/excluded.yaml'), ('unsaved-buffer','/new.yaml')]
        result(name, {'exact_symbol_set': actual == expected}, {'actual': actual, 'expected': expected, 'response': response})
    finally: lsp.close()

def main():
    try:
        run_rename('rename-dag-onexit', REN_DAG)
        steps = REN_DAG.replace('      dag:\n        tasks:\n          - name: run\n            template: work\n            onExit: cleanup', '      steps:\n        - - name: run\n            template: work\n            onExit: cleanup')
        run_rename('rename-step-onexit', steps)
        run_fix()
        run_fix(incomplete=True)
        run_scopes()
        run_unsupported()
        run_alias()
        run_workspace_symbols()
    except Exception as e:
        result('runner-error', {'completed': False}, {'error': repr(e), 'traceback': traceback.format_exc()})
    finally:
        save(OUT / 'manifest.json', {'command': sys.argv, 'pythonVersion': sys.version, 'binary': str(BINARY), 'binarySha256': sha(BINARY), 'officialArgoBinary': str(ARGO), 'officialArgoSha256': sha(ARGO), 'testSha256': sha(pathlib.Path(__file__)), 'expectationsSha256': sha(HERE/'wf-audit-gaps.expected.json'), 'inputs': {str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and ('/input/' in str(p) or p.name=='after.yaml')}, 'cases': len(RESULTS), 'passed': sum(r['pass'] for r in RESULTS), 'scope': 'CLI and standard LSP, no GUI or release acceptance'})
    return 0 if len(RESULTS) == 11 and all(r['pass'] for r in RESULTS) else 1

if __name__ == '__main__': sys.exit(main())
