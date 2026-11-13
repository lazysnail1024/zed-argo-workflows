#!/usr/bin/env python3
"""Independent black-box CLI/LSP audit. No production imports or expected-value derivation."""
import os, hashlib, json, pathlib, queue, subprocess, sys, threading, time, traceback, shutil

HERE = pathlib.Path(__file__).resolve().parent
BINARY = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else HERE / 'argo-workflows-ls').resolve()
OUT = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else HERE.parent.parent / 'artifacts/wf-local-rename').resolve()
OVERRIDE = ''
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
SELECTED = set(filter(None, os.environ.get("WF_RENAME_CASES", "").split(",")))
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
            (self.dest / 'lsp.stderr.raw').write_bytes(self.p.stderr.read())
            save(self.dest / 'traffic.json', {'sent': self.sent, 'received': self.received})
            save(self.dest / 'lsp.command.json', {'argv': [str(BINARY), 'serve', '--stdio'], 'exitCode': self.p.returncode})

DAG = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: example, namespace: argo}
spec:
  entrypoint: main
  arguments:
    parameters: [{name: global, value: hello}]
  templates:
    - name: main
      dag:
        target: produce consume
        tasks:
          - name: produce
            template: worker
          - name: consume
            template: reader
            depends: produce.Succeeded || produce.Skipped
            arguments:
              parameters: [{name: msg, value: "{{tasks.produce.outputs.parameters.answer}}"}]
              artifacts: [{name: file, from: "{{tasks.produce.outputs.artifacts.product}}"}]
      outputs:
        parameters:
          - name: summary
            valueFrom: {expression: "tasks['produce'].outputs.parameters['answer']"}
        artifacts:
          - name: gathered
            fromExpression: "tasks['produce'].outputs.artifacts['product']"
    - name: worker
      outputs:
        parameters: [{name: answer, valueFrom: {path: /tmp/value}}]
        artifacts: [{name: product, path: /tmp/data}]
      container:
        image: alpine
        args: ["{{workflow.parameters.global}}", "{{outputs.parameters.answer.path}}", "{{outputs.artifacts.product.path}}"]
    - name: reader
      inputs:
        parameters: [{name: msg}]
        artifacts: [{name: file, path: /tmp/input}]
      container:
        image: alpine
        args: ["{{inputs.parameters.msg}}", "{{=sprig.trim(inputs.parameters['msg'])}}", "{{inputs.artifacts.file.path}}"]
    - name: independent
      inputs: {parameters: [{name: msg, default: unrelated}]}
      container: {image: alpine, args: ["{{inputs.parameters.msg}}"]}
'''
STEPS = '''apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata: {name: steps-example}
spec:
  entrypoint: main
  templates:
    - name: main
      steps:
        - - name: produce
            template: worker
        - - name: consume
            template: worker
            when: "{{steps.produce.outputs.parameters.answer}} == ready"
      outputs:
        parameters:
          - name: summary
            valueFrom: {expression: "steps['produce'].outputs.parameters['answer']"}
    - name: worker
      outputs: {parameters: [{name: answer, valueFrom: {path: /tmp/value}}]}
      container: {image: alpine}
'''
FOREIGN='''---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata: {name: produce}
spec: {project: produce, source: {repoURL: https://example.com, path: produce}}
'''
def renamed(text,pairs):
    for before,after in pairs: text=text.replace(before,after)
    return text
TASK_PAIRS=[('target: produce consume','target: renamed consume'),('name: produce','name: renamed'),('produce.Succeeded','renamed.Succeeded'),('produce.Skipped','renamed.Skipped'),('tasks.produce.','tasks.renamed.'),("tasks['produce']","tasks['renamed']")]
INPUT_PAIRS=[('name: msg, value:','name: message, value:'),('parameters: [{name: msg}]','parameters: [{name: message}]'),('"{{inputs.parameters.msg}}", "{{=','"{{inputs.parameters.message}}", "{{='),("parameters['msg']","parameters['message']")]
OUTPUT_PAIRS=[('name: answer','name: resultvalue'),('.parameters.answer','.parameters.resultvalue'),("parameters['answer']","parameters['resultvalue']")]
ARTIFACT_PAIRS=[('name: product','name: outputfile'),('.artifacts.product','.artifacts.outputfile'),("artifacts['product']","artifacts['outputfile']")]

def run_case(name,text,needle,newname,expected,files=None,config=CONFIG,occurrence=0,offset=0,stale=False):
    if SELECTED and name not in SELECTED: return
    dest,work=setup(name,{'case.yaml':text,**(files or {})},config);client=LSP(dest,work);path=work/'case.yaml'
    try:
        ds=client.open(path,text);position=pos(text,needle,occurrence,offset);params={'textDocument':{'uri':path.as_uri()},'position':position}
        prepare=client.request('textDocument/prepareRename',params)
        response=client.request('textDocument/rename',params|{'newName':newname})
        actual=apply(text,response['result'],path.as_uri(),1) if 'documentChanges' in response.get('result',{}) else None
        checks={'disk_unchanged':path.read_bytes().decode()==text}
        if expected is None:
            checks['rename_refused']=response.get('error',{}).get('code')==-32602
        else:
            checks['prepare_succeeds']='range' in prepare.get('result',{})
            checks['exact_bytes']=actual==expected
            checks.update(no_new_errors=False, undo_restores_diagnostics=False)
            if stale: checks.update(old_version_ignored=False, changed_reference_not_old_binding=False)
            if actual is not None:
                after=client.change(path,actual,2);checks['no_new_errors']=not any(d['severity']==1 for d in after)
                undo=client.change(path,text,3);checks['undo_restores_diagnostics']=undo==ds
                if stale:
                    client.send('textDocument/didChange',{'textDocument':{'uri':path.as_uri(),'version':2},'contentChanges':[{'text':actual}]})
                    again=client.request('textDocument/rename',params|{'newName':newname})
                    checks['old_version_ignored']=apply(text,again['result'],path.as_uri(),3)==expected if 'documentChanges' in again.get('result',{}) else False
                    unknown=text.replace('name: produce','name: different')
                    client.change(path,unknown,4)
                    fresh=client.request('textDocument/rename',params|{'newName':newname})
                    checks['changed_reference_not_old_binding']='error' in fresh or fresh.get('result') is None
        result(name,checks,{'before':ds,'prepare':prepare,'response':response,'expected':expected,'actual':actual})
    finally:client.close()

try:
    run_case('task-declaration',DAG,'name: produce','renamed',renamed(DAG,TASK_PAIRS),offset=6)
    run_case('task-static-reference',DAG,'tasks.produce','renamed',renamed(DAG,TASK_PAIRS),offset=7,stale=True)
    run_case('task-dependencies',DAG.replace('depends: produce.Succeeded || produce.Skipped','dependencies: [produce]'),'name: produce','renamed',renamed(DAG.replace('depends: produce.Succeeded || produce.Skipped','dependencies: [produce]'),TASK_PAIRS+[('dependencies: [produce]','dependencies: [renamed]')]),offset=6)
    run_case('step-reference',STEPS,'steps.produce','renamed',renamed(STEPS,[('name: produce','name: renamed'),('steps.produce.','steps.renamed.'),("steps['produce']","steps['renamed']")]),offset=7)
    run_case('input-parameter-call-argument',DAG,'name: msg, value:','message',renamed(DAG,INPUT_PAIRS),offset=6)
    run_case('input-parameter-expression',DAG,"parameters['msg']",'message',renamed(DAG,INPUT_PAIRS),offset=12)
    run_case('output-parameter',DAG,'name: answer','resultvalue',renamed(DAG,OUTPUT_PAIRS),offset=6)
    run_case('output-artifact-expression',DAG,"artifacts['product']",'outputfile',renamed(DAG,ARTIFACT_PAIRS),offset=11)
    run_case('input-artifact',DAG,'name: file, from:','inputfile',renamed(DAG,[('name: file','name: inputfile'),('inputs.artifacts.file','inputs.artifacts.inputfile')]),offset=6)
    run_case('workflow-parameter',DAG,'name: global','globalvalue',renamed(DAG,[('name: global','name: globalvalue'),('workflow.parameters.global','workflow.parameters.globalvalue')]),offset=6)
    run_case('template-with-static-tags',DAG,'template: worker','renamed',renamed(DAG,[('name: worker','name: renamed'),('template: worker','template: renamed')]),offset=11)
    cron=DAG.replace('kind: Workflow','kind: CronWorkflow').replace('spec:\n','spec:\n  schedule: "0 * * * *"\n  workflowSpec:\n').replace('  entrypoint:','    entrypoint:')
    # Indent the complete workflowSpec body by two additional spaces.
    cron=DAG.replace('kind: Workflow','kind: CronWorkflow').split('spec:\n')[0]+'spec:\n  schedule: "0 * * * *"\n  workflowSpec:\n'+''.join('  '+line for line in DAG.split('spec:\n',1)[1].splitlines(keepends=True))
    run_case('cron-artifact',cron,'name: product','outputfile',renamed(cron,ARTIFACT_PAIRS),offset=6)
    run_case('mixed-foreign-and-unrelated',DAG+FOREIGN+'---\n'+STEPS,'tasks.produce','renamed',renamed(DAG,TASK_PAIRS)+FOREIGN+'---\n'+STEPS,offset=7,files={'unrelated.yaml':STEPS})
    unicode=DAG.replace('args: ["{{workflow','args: ["\U0001f40c {{workflow').replace('\n','\r\n')
    run_case('unicode-crlf',unicode,'workflow.parameters.global','globalvalue',renamed(unicode,[('name: global','name: globalvalue'),('workflow.parameters.global','workflow.parameters.globalvalue')]),offset=22)
    unrelated=DAG.replace('container: {image: alpine, args: ["{{inputs.parameters.msg}}"]}', 'container: {image: alpine, args: ["{{workflow.uid}}", "{{=inputs.parameters[workflow.parameters.global]}}"]}')
    run_case('unrelated-dynamic-template',unrelated,'name: produce','renamed',renamed(unrelated,TASK_PAIRS),offset=6)
    run_case('computed-index-workflow-reference',unrelated,'name: global','globalvalue',renamed(unrelated,[('name: global','name: globalvalue'),('workflow.parameters.global','workflow.parameters.globalvalue')]),offset=6)
    targeted=DAG.replace("inputs.parameters['msg']",'inputs.parameters[workflow.parameters.global]')
    run_case('computed-index-target-refused',targeted,'name: msg, value:','message',None,offset=6)
    raw=DAG.replace("valueFrom: {expression: \"tasks['produce'].outputs.parameters['answer']\"}", 'valueFrom: {parameter: tasks.produce.outputs.parameters.answer}').replace("fromExpression: \"tasks['produce'].outputs.artifacts['product']\"", 'from: tasks.produce.outputs.artifacts.product')
    run_case('raw-output-parameter-reference',raw,'name: answer','resultvalue',renamed(raw,OUTPUT_PAIRS),offset=6)
    run_case('raw-artifact-reference',raw,'name: product','outputfile',renamed(raw,ARTIFACT_PAIRS),offset=6)
    hyphen=DAG.replace("\"{{=sprig.trim(inputs.parameters['msg'])}}\"", '"{{=inputs.parameters.msg}}"')
    hyphenExpected=renamed(hyphen,[(a,b.replace('message','msg-new')) for a,b in INPUT_PAIRS]).replace('{{=inputs.parameters.msg}}', "{{=inputs.parameters['msg-new']}}")
    run_case('expression-dot-to-string-index',hyphen,'name: msg, value:','msg-new',hyphenExpected,offset=6)
    run_case('unknown-member',DAG.replace('tasks.produce.outputs.parameters.answer','tasks.produce.unknown'),'name: produce','renamed',None,offset=6)
    run_case('incomplete-sibling-scope',DAG,'name: produce','renamed',renamed(DAG,TASK_PAIRS),offset=6,files={'broken.yaml':'apiVersion: [\n'},config=CONFIG+'scopes:\n  - name: main\n    include: ["case.yaml"]\n  - name: other\n    include: ["broken.yaml"]\n')
    coupled=DAG.replace('    - name: main\n','    - name: main\n      inputs: {parameters: [{name: global}]}\n')
    run_case('coupled-workflow-entrypoint',coupled,'name: global','renamed',None,offset=6)
    run_case('index-needs-expression-tag',DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}", "{{inputs.parameters['msg']}}"),'name: msg, value:','message',None,offset=6)
    literal=DAG.replace('args: ["{{workflow.parameters.global}}"', 'args: ["{{= \'workflow.parameters.global\' }}", "{{workflow.parameters.global}}"')
    run_case('expression-string-literal-unchanged',literal,'name: global','globalvalue',renamed(literal,[('name: global','name: globalvalue'),('{{workflow.parameters.global}}','{{workflow.parameters.globalvalue}}')]),offset=6)
    step_hook=STEPS.replace('            template: worker\n',"            template: worker\n            hooks:\n              running:\n                expression: steps['produce'].status == 'Running'\n                template: notify\n",1)+'    - name: notify\n      container: {image: alpine}\n'
    step_pairs=[('name: produce','name: renamed'),('steps.produce.','steps.renamed.'),("steps['produce']","steps['renamed']")]
    run_case('step-self-hook-declaration',step_hook,'name: produce','renamed',renamed(step_hook,step_pairs),offset=6)
    run_case('step-self-hook-reference',step_hook,"steps['produce']",'renamed',renamed(step_hook,step_pairs),offset=7)
    malformed_tail=DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}",'{{=inputs.parameters.msg +}}')
    run_case('malformed-expression-tail',malformed_tail,'name: msg, value:','message',None,offset=6)
    malformed_paren=DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}",'{{= (inputs.parameters.msg }}')
    run_case('malformed-expression-parenthesis',malformed_paren,'name: msg, value:','message',None,offset=6)
    malformed_raw=DAG.replace("tasks['produce'].outputs.parameters['answer']", "tasks['produce'].outputs.parameters['answer'] +")
    run_case('malformed-raw-expression',malformed_raw,'name: produce','renamed',None,offset=6)
    for case, literal in [('escaped', r'"x\ninputs.parameters.msg"'), ('raw', '`inputs.parameters.msg`')]:
        literal_text=DAG.replace('"{{=sprig.trim(inputs.parameters[\'msg\'])}}"', "'{{= "+literal+" }}'")
        literal_expected=renamed(literal_text,[('name: msg, value:','name: message, value:'),('parameters: [{name: msg}]','parameters: [{name: message}]'),('"{{inputs.parameters.msg}}",','"{{inputs.parameters.message}}",')])
        run_case('expression-'+case+'-literal',literal_text,'name: msg, value:','message',literal_expected,offset=6)
    comment=DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}", '{{=inputs.parameters.msg /* inputs.parameters.msg */}}')
    comment_expected=renamed(comment,INPUT_PAIRS).replace('{{=inputs.parameters.msg /*','{{=inputs.parameters.message /*')
    run_case('expression-comment-preserved',comment,'name: msg, value:','message',comment_expected,offset=6)
    expr_unicode=DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}", "{{='\U0001f40c ' + inputs.parameters.msg}}")
    expr_unicode_expected=renamed(expr_unicode,INPUT_PAIRS).replace("+ inputs.parameters.msg}}", "+ inputs.parameters.message}}")
    run_case('expression-unicode-before-member',expr_unicode,'+ inputs.parameters.msg','message',expr_unicode_expected,offset=20)
    shadow=DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}", "{{=let inputs = {parameters: {msg: 'local'} }; inputs.parameters.msg}}")
    shadow_expected=renamed(shadow,[('name: msg, value:','name: message, value:'),('parameters: [{name: msg}]','parameters: [{name: message}]'),('"{{inputs.parameters.msg}}",','"{{inputs.parameters.message}}",')])
    run_case('expression-shadowed-input',shadow,'name: msg, value:','message',shadow_expected,offset=6)
    initializer=DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}", '{{=let inputs = inputs.parameters.msg; inputs}}')
    initializer_expected=renamed(initializer,INPUT_PAIRS).replace('= inputs.parameters.msg;','= inputs.parameters.message;')
    run_case('expression-shadow-initializer',initializer,'name: msg, value:','message',initializer_expected,offset=6)
    unrelated_let=DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}", "{{=let prefix = 'hello'; prefix + inputs.parameters.msg}}")
    unrelated_let_expected=renamed(unrelated_let,INPUT_PAIRS).replace('+ inputs.parameters.msg}}','+ inputs.parameters.message}}')
    run_case('expression-unrelated-let',unrelated_let,'name: msg, value:','message',unrelated_let_expected,offset=6)
    for case, expression in [
        ('static', "$env['inputs'].parameters.msg"),
        ('dynamic', '$env[workflow.name].parameters.msg'),
        ('get', "get($env, 'inputs').parameters.msg"),
        ('shadow', "let inputs = {parameters: {msg: 'local'} }; $env['inputs'].parameters.msg"),
        ('alias', "let external = $env; external.inputs.parameters.msg"),
    ]:
        env_text=DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}", '{{='+expression+'}}')
        run_case('env-'+case+'-refusal',env_text,'name: msg, value:','message',None,offset=6)
    safe_env=DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}", "{{=$env['workflow'].uid}}")
    safe_env_expected=renamed(safe_env,INPUT_PAIRS)
    run_case('env-unrelated-namespace',safe_env,'name: msg, value:','message',safe_env_expected,offset=6)
    literal_env=DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}", "{{='get($env, inputs).parameters.msg'}}")
    run_case('env-literal-preserved',literal_env,'name: msg, value:','message',renamed(literal_env,INPUT_PAIRS),offset=6)
    env_selector=DAG.replace("{{=sprig.trim(inputs.parameters['msg'])}}", "{{=$env['inputs'].parameters[workflow.parameters.global]}}")
    run_case('env-selector-reference',env_selector,'name: global','globalvalue',renamed(env_selector,[('name: global','name: globalvalue'),('workflow.parameters.global','workflow.parameters.globalvalue')]),offset=6)
    run_case('workflow-reserved-json',DAG,'name: global','json',None,offset=6)
    run_case('collision',DAG,'name: produce','consume',None,offset=6)
    run_case('public',DAG.replace('kind: Workflow','kind: WorkflowTemplate'),'name: produce','renamed',None,offset=6)
    run_case('derived-alias',DAG.replace('name: msg}]','name: msg, default: &x literal}]').replace('"{{inputs.parameters.msg}}"','*x'),'name: produce','renamed',None,offset=6)
    run_case('dynamic-index',DAG.replace("tasks['produce']","tasks[workflow.parameters.global]"),'name: produce','renamed',None,offset=6)
    run_case('unknown-static',DAG.replace('tasks.produce.outputs.parameters.answer','tasks.unknown.outputs.parameters.answer'),'name: produce','renamed',None,offset=6)
    run_case('unknown-collection',DAG.replace("inputs.parameters['msg']","inputs.parameters"),'name: msg, value:','message',None,offset=6)
    run_case('external-template',DAG.replace('template: worker','templateRef: {name: shared, template: worker}'),'name: answer','resultvalue',None,offset=6)
    run_case('incomplete-same-scope',DAG,'name: produce','renamed',None,offset=6,files={'broken.yaml':'apiVersion: [\n'})
    run_case('duplicate-declaration',DAG.replace('name: consume','name: produce'),'name: produce','renamed',None,offset=6)
    run_case('global-output',DAG.replace('name: answer, valueFrom','name: answer, globalName: public, valueFrom'),'name: answer','resultvalue',None,offset=6)
except Exception:
    result('runner-error',{'exception':False},{'traceback':traceback.format_exc()})
finally:
    save(OUT/'manifest.json',{'selectedCases':sorted(SELECTED),'command':[sys.executable,str(pathlib.Path(__file__).resolve()),str(BINARY),str(OUT)],'pythonVersion':sys.version,'binarySha256':sha(BINARY),'testSha256':sha(pathlib.Path(__file__)),'expectedSha256':sha(HERE/'wf-local-rename.expected.json'),'files':{str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and p.name not in ('manifest.json','results.json')}})
    sys.exit(0 if len(RESULTS)==(len(SELECTED) if SELECTED else 56) and all(r['pass'] for r in RESULTS) else 1)
