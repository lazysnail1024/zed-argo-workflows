"""Real process probe for the new key-filter's unrelated-sibling behavior."""
import importlib.util, json, pathlib, sys, traceback

root = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wire', root/'tests/e2e/wf-audit-gaps.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Client(m.LSP):
    def send(self, method, params=None, ident=None):
        frame = {'jsonrpc':'2.0','method':method}
        if params is not None: frame['params'] = params
        if ident is not None: frame['id'] = ident
        payload = json.dumps(frame).encode()
        with (self.dest/'lsp.stdin.raw').open('ab') as f:
            f.write(('Content-Length: %d\r\n\r\n'%len(payload)).encode()+payload)
        return super().send(method,params,ident)

for name, strategy, diagnostics in [('unrelated-invalid-enum-key','Maybe',[('SCHEMA_VALIDATION','Error')]),('valid-enum-key-positive','Never',[])]:
    marked = 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: independent}\nspec:\n  artifactGC:\n    strategy: '+strategy+'\n    fo<CURSOR>: true\n'
    text = marked.replace('<CURSOR>','')
    expected = text.replace('fo: true','forceFinalizerRemoval: true')
    dest, work = m.setup(name,{'case.yaml':text})
    path = work/'case.yaml'
    (dest/'original.yaml').write_bytes(text.encode())
    (dest/'expected.yaml').write_bytes(expected.encode())
    m.save(dest/'expectations.json',{'labels':['forceFinalizerRemoval'],'diagnostics':[list(x) for x in diagnostics],'filesAnalyzed':1,'resourcesAnalyzed':1,'exitCode':int(bool(diagnostics)),'analysisComplete':True,'exactReplacement':['fo: true','forceFinalizerRemoval: true']})
    client = None
    try:
        client = Client(dest,work)
        client.open(path,text)
        response = client.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':m.pos(marked,'<CURSOR>')})
        items = response.get('result') or []
        checks = {'textEditPresent':len(items)==1 and 'textEdit' in items[0], 'noProtocolError':'error' not in response,'exactLabels':[x['label'] for x in items]==['forceFinalizerRemoval']}
        checks['exactUnrelatedBytes'] = False
        for check in ('exitCounts', 'exactDiagnostics', 'noConfigurationErrors', 'honestIncomplete'):
            checks['actual-' + check] = False
        actual = None
        if len(items)==1 and 'textEdit' in items[0]:
            actual = m.apply(text,{'documentChanges':[{'textDocument':{'uri':path.as_uri(),'version':1},'edits':[items[0]['textEdit']]}]},path.as_uri(),1)
            (dest/'actual.yaml').write_bytes(actual.encode())
            checks['exactUnrelatedBytes'] = actual == expected
        for kind,data in [('expected',expected)]+([('actual',actual)] if actual is not None else []):
            path.write_bytes(data.encode())
            run = m.command(dest,kind+'-check',[m.BINARY,'check','--root',work,'--format','json'])
            report = json.loads(run.stdout)
            checks[kind+'-exitCounts'] = run.returncode == int(bool(diagnostics)) and report['filesAnalyzed']==1 and report['resourcesAnalyzed']==1
            checks[kind+'-exactDiagnostics'] = [(x['code'],x['severity']) for x in report['diagnostics']] == diagnostics
            checks[kind+'-noConfigurationErrors'] = report['configurationErrors']==[]
            checks[kind+'-honestIncomplete'] = report['analysisComplete'] is True and report['notEvaluated']==[]
        m.result(name,checks,{'response':response})
    except Exception:
        (dest/'error.txt').write_text(traceback.format_exc())
        m.result(name,{'completed':False})
    finally:
        if client: client.close()
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'protocolHelperSha256':m.sha(root/'tests/e2e/wf-audit-gaps.py'),'caseCount':len(m.RESULTS),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}})
raise SystemExit(0 if len(m.RESULTS)==2 and all(x['pass'] for x in m.RESULTS) else 1)
