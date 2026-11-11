#!/usr/bin/env python3
"""Independent black-box CLI/LSP audit. No production imports or expected-value derivation."""
import hashlib, json, pathlib, queue, subprocess, sys, threading, time, traceback, shutil

HERE = pathlib.Path(__file__).resolve().parent
BINARY = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE / 'argo-workflows-ls').resolve()
OUT = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else HERE.parent.parent / 'artifacts/wf-quickfixes').resolve()
OVERRIDE = ''
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
    r = subprocess.run([str(a) for a in argv], capture_output=True, timeout=timeout, **kwargs)
    (dest / (label + '.stdout')).write_bytes(r.stdout)
    (dest / (label + '.stderr')).write_bytes(r.stderr)
    save(dest / (label + '.command.json'), {'argv': [str(a) for a in argv], 'exitCode': r.returncode})
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
        if p['line']==len(lines) and p['character']==0: return len(text)
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
        self.next_id = 0
        self.p = subprocess.Popen([str(BINARY), 'serve', '--stdio'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.stderr_raw = bytearray()
        def read_stderr():
            while data := self.p.stderr.read1(4096):
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
                    self.received.append(value)
                    self.q.put(value)
            except Exception as e: self.q.put(e)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        self.init = self.request('initialize', {'rootUri': work.as_uri(), 'initializationOptions': {'targetVersion': OVERRIDE}, 'capabilities': {'workspace': {'workspaceEdit': {'documentChanges': True}}}})
        self.send('initialized', {})
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
            value = self.q.get(timeout=max(.01, deadline - time.monotonic()))
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
                self.request('shutdown')
                self.send('exit')
                self.p.wait(timeout=5)
        finally:
            if self.p.poll() is None: self.p.kill(); self.p.wait()
            self.reader.join(timeout=1)
            (self.dest / 'lsp.stdout.raw').write_bytes(self.raw)
            self.stderr_reader.join(timeout=1)
            (self.dest / 'lsp.stderr.raw').write_bytes(self.stderr_raw)
            save(self.dest / 'traffic.json', {'sent': self.sent, 'received': self.received})
            save(self.dest / 'lsp.command.json', {'argv': [str(BINARY), 'serve', '--stdio'], 'exitCode': self.p.returncode})

BASE = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: test, namespace: argo}
spec:
  entrypoint: missing
  templates:
    - name: first
      container: {image: alpine}
    - name: second
      container: {image: alpine}
    - name: unsafe
      inputs: {parameters: [{name: required}]}
      container: {image: alpine}
'''
ENUM = BASE.replace('entrypoint: missing', 'entrypoint: first\n  artifactGC: {strategy: Bogus}')
EXPECTED = {
 'local': ['"first"', '"second"'],
 'enum': ['""', '"Never"', '"OnWorkflowCompletion"', '"OnWorkflowDeletion"'],
 'external': ['"library"'],
}

def actions(client, path, text, needle, diagnostics=None):
    p = pos(text, needle)
    return client.request('textDocument/codeAction', {'textDocument': {'uri': path.as_uri()}, 'range': {'start': p, 'end': p}, 'context': {'diagnostics': diagnostics or []}})
def edits(client, response, text, path, version=1):
    outputs = []
    for action in response.get('result', []):
        resolved = client.request('codeAction/resolve', action)
        assert 'result' in resolved, resolved
        outputs.append(apply(text, resolved['result']['edit'], path.as_uri(), version))
    return outputs

def scalar_case(name, text, needle, expected, files=None, config=CONFIG, stale=False, incomplete=False):
    dest, work = setup(name, {'case.yaml': text, **(files or {})}, config)
    client = LSP(dest, work)
    try:
        path = work/'case.yaml'; ds = client.open(path,text)
        response = actions(client,path,text,needle,ds)
        got = edits(client,response,text,path)
        assertions = {'request_succeeded':isinstance(response.get('result'),list),'exact_candidate_bytes': sorted(got)==sorted(expected), 'disk_unchanged':path.read_bytes().decode()==text}
        if expected and got:
            if stale:
                dependency=work/'new.yaml'
                client.open(dependency,'apiVersion: v1\nkind: ConfigMap\nmetadata: {name: changed}\n')
                old=client.request('codeAction/resolve',response['result'][0])
                assertions['independent_peer_keeps_local_edit']='result' in old and apply(text,old['result']['edit'],path.as_uri(),1)==got[0]
            newds=client.change(path,got[0],2)
            cliwork=dest/'after-input'
            if cliwork.exists(): shutil.rmtree(cliwork)
            shutil.copytree(work,cliwork)
            (cliwork/'case.yaml').write_bytes(got[0].encode())
            exitcode,report=cli(dest,cliwork)
            if incomplete:
                assertions['incomplete_peer_still_reported']=exitcode==2 and report['analysisComplete'] is False and bool(report['notEvaluated'])
            else:
                assertions['cli_after_contract']=exitcode==0 and report['analysisComplete'] is True and not any(d['severity']=='Error' for d in report['diagnostics'])
            assertions['target_removed']=not any(d['code'] in ('WF_LOCAL_TEMPLATE_MISSING','WF_TEMPLATE_REF_UNRESOLVED','SCHEMA_VALIDATION') for d in newds)
            if stale:
                old=client.request('codeAction/resolve',response['result'][0])
                assertions['stale_rejected']=old.get('error',{}).get('code')==-32801
        result(name,assertions,{'diagnostics':ds,'response':response,'after':got,'expected':expected})
    finally:client.close()

def pin_case(name,config,needle,expected,override='',change=False,closed=False,expected_version='3.6.0'):
    global OVERRIDE
    OVERRIDE=override
    dest,work=setup(name,{'dev/case.yaml':BASE.replace('missing','first')},config)
    disk_before={p.relative_to(work):p.read_bytes() for p in work.rglob('*') if p.is_file()}
    client=LSP(dest,work)
    try:
        resource=work/'dev/case.yaml'; client.open(resource,resource.read_text())
        path=work/'.argo-workflows-ls.yaml'
        if not closed:
            marker = len(client.stderr_raw)
            client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':7,'languageId':'yaml','text':config}})
            # Metadata refresh is asynchronous. Both offered and refused pins
            # are judged only after the current small fixture finishes discovery.
            deadline = time.monotonic() + 5
            ready = False
            while time.monotonic() < deadline and not ready:
                for line in bytes(client.stderr_raw)[marker:].splitlines():
                    try:
                        state = json.loads(line)
                        ready = ready or state.get('phase') == 'index' and state.get('state') in ('Ready', 'Limited')
                    except (ValueError, UnicodeError):
                        pass
                if not ready: time.sleep(.01)
            assert ready, 'configuration refresh did not finish'
        response=actions(client,path,config,needle)
        got=edits(client,response,config,path,7) if not closed else []
        assertions={'request_succeeded':closed or isinstance(response.get('result'),list),'exact_pin_bytes':got==expected,'disk_unchanged':{p.relative_to(work):p.read_bytes() for p in work.rglob('*') if p.is_file()}==disk_before}
        if not expected and not closed:assertions['no_pin_action']=response.get('result')==[]
        if closed:assertions['closed_refused']='error' in response or response.get('result')==[]
        if got:
            persisted=dest/'persisted-input'
            if persisted.exists(): shutil.rmtree(persisted)
            shutil.copytree(work,persisted)
            (persisted/'.argo-workflows-ls.yaml').write_bytes(got[0].encode())
            code,report=cli(dest,persisted,'pin-without-override')
            assertions['persisted_without_override']=code==0 and report['configurationErrors']==[] and report['resourcesAnalyzed']==1 and any(t['version']==expected_version for t in report['targets'])
            client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':8},'contentChanges':[{'text':config+'# changed\n' if change else got[0]}]})
            old=client.request('codeAction/resolve',response['result'][0])
            assertions['stale_rejected']=old.get('error',{}).get('code')==-32801
            current=client.request('textDocument/hover',{'textDocument':{'uri':resource.as_uri()},'position':{'line':4,'character':4}})
            assertions['target_preserved']=expected_version in json.dumps(current)
        result(name,assertions,{'response':response,'after':got,'expected':expected})
    finally:client.close(); OVERRIDE=''

try:
    scalar_case('reference-local-candidates',BASE,'missing',[BASE.replace('missing',v) for v in EXPECTED['local']],stale=True)
    scalar_case('reference-quoted-crlf',BASE.replace('missing',"'missing'").replace('\n','\r\n'),'missing',[BASE.replace('missing',v).replace('\n','\r\n') for v in EXPECTED['local']])
    scalar_case('reference-dynamic',BASE.replace('missing','"{{workflow.parameters.pick}}"'),'workflow.parameters',[])
    scalar_case('reference-incomplete-scope',BASE,'missing',[BASE.replace('missing',v) for v in EXPECTED['local']],{'broken.yaml':'apiVersion: [\n'},incomplete=True)
    scalar_case('reference-sibling-resource',BASE+'---\n'+BASE.replace('missing','only-other').replace('name: first','name: only-other'),'missing',[BASE.replace('missing',v)+'---\n'+BASE.replace('missing','only-other').replace('name: first','name: only-other') for v in EXPECTED['local']])
    scalar_case('enum-candidates',ENUM,'Bogus',[ENUM.replace('Bogus',v) for v in EXPECTED['enum']],stale=True)
    scalar_case('enum-valid',ENUM.replace('Bogus','Never'),'Never',[])
    scalar_case('enum-block-scalar',ENUM.replace('artifactGC: {strategy: Bogus}','artifactGC:\n    strategy: |-\n      Bogus'),'Bogus',[])
    scalar_case('enum-foreign-gvk',ENUM.replace('argoproj.io/v1alpha1','foreign.io/v1'),'Bogus',[])
    external='''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: caller, namespace: argo}
spec:
  workflowTemplateRef: {name: absent}
'''
    library=BASE.replace('kind: Workflow','kind: WorkflowTemplate').replace('name: test','name: library').replace('missing','first')
    scalar_case('external-resource-candidate',external,'absent',[external.replace('absent','"library"')],{'library.yaml':library,'wrong-namespace.yaml':library.replace('name: library','name: elsewhere').replace('namespace: argo','namespace: other')})
    scalar_case('external-ambiguous',external,'absent',[],{'one.yaml':library,'two.yaml':library})
    scope='schemaVersion: 1\ntargetVersion: "3.6.0"\nscopes:\n  - name: dev\n    include: ["dev/**"]\n'
    namedroot=scope.replace('name: dev','name: root')
    pin_case('pin-scope-named-root',namedroot,'name: root',[namedroot+'    targetVersion: "3.6.0"\n'])
    pin_case('pin-scope-inherited',scope,'name: dev',[scope+'    targetVersion: "3.6.0"\n'],change=True)
    pin_case('pin-root-override','schemaVersion: 1\n# keep me\n','schemaVersion',['schemaVersion: 1\n# keep me\ntargetVersion: "3.6.0"\n'],override='3.6.0')
    ended='schemaVersion: 1\n# keep before end\n...\n# keep after end\n'
    pin_case('pin-root-document-end',ended,'schemaVersion',['schemaVersion: 1\n# keep before end\ntargetVersion: "3.6.0"\n...\n# keep after end\n'],override='3.6.0')
    pin_case('pin-root-document-end-crlf',ended.replace('\n','\r\n'),'schemaVersion',['schemaVersion: 1\r\n# keep before end\r\ntargetVersion: "3.6.0"\r\n...\r\n# keep after end\r\n'],override='3.6.0')
    pin_case('pin-scope-document-end',scope+'...\n','name: dev',[scope+'    targetVersion: "3.6.0"\n...\n'])
    pin_case('pin-existing' ,CONFIG,'targetVersion',[])
    pin_case('pin-closed',scope,'name: dev',[],closed=True)
    two=scope+'  - name: prod\n    include: ["prod/**"]\n    targetVersion: "3.5.0"\n'
    pin_case('pin-scope-isolation',two,'name: dev',[scope+'    targetVersion: "3.6.0"\n'+'  - name: prod\n    include: ["prod/**"]\n    targetVersion: "3.5.0"\n'])
    pin_case('pin-root-replace',CONFIG.replace('3.6.0','3.5.0'),'targetVersion',[CONFIG],override='3.6.0')
    pin_case('pin-flow-refused','{schemaVersion: 1}\n','schemaVersion',[],override='3.6.0')
    alias=ENUM.replace('  artifactGC: {strategy: Bogus}', '  artifactGC: {strategy: *bad}').replace('spec:', 'x: &bad Bogus\nspec:')
    scalar_case('enum-alias-refused',alias,'strategy',[])
    scalar_case('reference-scope-isolation',external,'absent',[],{'other/library.yaml':library},'schemaVersion: 1\ntargetVersion: "3.6.0"\nscopes:\n  - name: caller\n    include: ["case.yaml"]\n  - name: other\n    include: ["other/**"]\n')
    pin_case('pin-known-default','schemaVersion: 1\n','schemaVersion',[])
    pin_case('pin-explicit-auto','schemaVersion: 1\ntargetVersion: "auto"\n','targetVersion',[])
    pin_case('pin-auto-override',CONFIG,'targetVersion',[],override='auto')
    pin_case('pin-auto-inherited-scope',scope.replace('3.6.0','auto'),'name: dev',[])
    pin_case('pin-explicit-auto-scope',scope+'    targetVersion: "auto"\n','name: dev',[])
    pin_case('pin-root-older-exact','schemaVersion: 1\n','schemaVersion',['schemaVersion: 1\ntargetVersion: "3.5.0"\n'],override='3.5.0',expected_version='3.5.0')
    pin_case('pin-root-latest-exact','schemaVersion: 1\n','schemaVersion',['schemaVersion: 1\ntargetVersion: "4.1.6"\n'],override='4.1.6',expected_version='4.1.6')
except Exception:
    result('runner-error',{'exception':False},{'traceback':traceback.format_exc()})
finally:
    files={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and p.name not in ('manifest.json','results.json')}
    save(OUT/'manifest.json',{'command':[sys.executable,str(pathlib.Path(__file__).resolve()),str(BINARY),str(OUT)],'pythonVersion':sys.version,'binarySha256':sha(BINARY),'testSha256':sha(pathlib.Path(__file__)),'expectedSha256':sha(HERE/'wf-quickfixes.expected.json'),'files':files})
    (OUT/'rerun.txt').write_text(f'python3 {pathlib.Path(__file__).resolve()} {BINARY} {OUT}\n')
    sys.exit(0 if len(RESULTS)==31 and all(r['pass'] for r in RESULTS) else 1)
