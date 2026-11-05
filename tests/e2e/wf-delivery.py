#!/usr/bin/env python3
"""Automatic and strict release delivery policy through real CLI and LSP consumers."""
from pathlib import Path
import importlib.util, json, re, sys, threading, traceback
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('wire', HERE / 'wf-audit-gaps.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
VALID = 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: delivery}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: alpine}\n'
CONFIG = 'schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\n'
CATALOG = HERE.parents[1] / 'compat/catalog.lock.json'
LOCK = json.loads(CATALOG.read_text())
SCHEMAS = {r['version']: r['schemaSha256'] for r in LOCK['versions']}
SCHEMAS['auto'] = LOCK['automatic']['schemaSha256']
CASES = ['default', 'explicit', 'error', 'warning', 'warnings-as-errors', 'experimental', 'empty-experimental', 'unknown', 'mixed', 'selected', 'file-filter', 'edits', 'settings', 'pin-mixed']

def norm(rows): return sorted(json.dumps(x, sort_keys=True) for x in rows)
def diagnostic(path, text, needle, code, message, severity, target):
    start = m.pos(text, needle)
    return {'uri': path.as_uri(), 'range': {'start': start, 'end': start | {'character': start['character'] + len(needle)}}, 'code': code, 'message': message, 'severity': severity, 'source': 'argo-workflows', 'positionKind': 'exact', 'targetId': target}
def published(rows): return [{k:d[k] for k in ['range','code','message','source']} | {'severity': {'Error':1,'Warning':2}[d['severity']]} for d in rows]

def prepare(name):
    dest = m.OUT / name; work = dest / 'input'; work.mkdir(parents=True, exist_ok=False)
    text = VALID.replace('entrypoint: main','entrypoint: absent') if name=='error' else VALID
    if name in ['warning','warnings-as-errors']: text = text.replace('  entrypoint:', '  unknownSetting: true\n  entrypoint:')
    mixed = name in ['mixed','selected','file-filter','edits','pin-mixed']
    config = CONFIG
    if mixed:
        config += 'scopes:\n  - name: stable\n    include: ["stable/*.yaml"]\n  - name: preview\n    include: ["preview/*.yaml"]\n    targetVersion: "3.5.0"\n'
    if name in ['experimental','empty-experimental']: config=config.replace('3.6.0','3.5.0')
    if name=='unknown': config=config.replace('3.6.0','9.9.9')
    if name!='default': (work/'.argo-workflows-ls.yaml').write_bytes(config.encode('utf-8'))
    path=work/('stable/case.yaml' if mixed else 'case.yaml');path.parent.mkdir(exist_ok=True);path.write_bytes(text.encode('utf-8'))
    if name=='empty-experimental': path.unlink()
    if mixed:
        peer=work/'preview/case.yaml';peer.parent.mkdir();peer.write_bytes(VALID.encode('utf-8'))
    code=1 if name in ['error','warnings-as-errors'] else 2 if name=='unknown' else 0
    complete=code!=2; diagnostics=[];target=str(work)+('#stable' if mixed else '#root')
    if name=='error': diagnostics=[diagnostic(path,text,'absent','WF_LOCAL_TEMPLATE_MISSING','local template does not exist: absent','Error',target)]
    if name in ['warning','warnings-as-errors']: diagnostics=[diagnostic(path,text,'unknownSetting','SCHEMA_UNKNOWN_FIELD','field "unknownSetting" is not defined for target 3.6.0','Warning',target)]
    expected={'exitCode':code,'diagnostics':diagnostics,'analysisComplete':complete,'configurationErrors':['unknown target version 9.9.9 with accepted-target recovery guidance'] if name=='unknown' else [],'notEvaluated':[],'filesAnalyzed':0 if name in ['unknown','empty-experimental'] else 2 if name in ['mixed','file-filter'] else 1,'resourcesAnalyzed':0 if name in ['unknown','empty-experimental'] else 2 if name in ['mixed','file-filter'] else 1}
    expected['versions']=[] if name in ['unknown','empty-experimental'] else ['3.6.0','3.5.0'] if name in ['mixed','file-filter'] else ['3.5.0'] if name=='experimental' else ['auto'] if name=='default' else ['3.6.0']
    m.save(dest/'expected.json',expected)
    return dest,work,path,text,expected

def cli_case(name):
    dest,work,path,text,expected=prepare(name)
    argv=[m.BINARY,'check','--root',work,'--format','json']
    if name=='warnings-as-errors': argv+=['--warnings-as-errors']
    if name=='selected': argv+=['--scope','stable']
    if name=='file-filter': argv+=['--file',path]
    r=m.command(dest,'check',argv);raw=json.loads(r.stdout)
    checks={'exit':r.returncode==expected['exitCode'],'diagnostics':norm(raw['diagnostics'])==norm(expected['diagnostics']), 'envelope':all(raw.get(k)==expected[k] for k in ['analysisComplete','notEvaluated','filesAnalyzed','resourcesAnalyzed']) and (len(raw['configurationErrors'])==1 and 'unknown target version: 9.9.9' in raw['configurationErrors'][0] and 'auto' in raw['configurationErrors'][0] and 'override' in raw['configurationErrors'][0] if name=='unknown' else raw['configurationErrors']==[]), 'versions':sorted(t['version'] for t in raw['targets'])==sorted(expected['versions']), 'identity':raw['product']=='argo-workflows' and raw['reportSchemaVersion']==1 and raw['catalogHash']==m.sha(CATALOG) and raw['validationScope']=='local-editing-contract', 'targetIdentity':all(t.get('version') in SCHEMAS and t.get('schemaHash')==SCHEMAS[t['version']] and t.get('experimental') is False and bool(re.fullmatch('[0-9a-f]{64}',t['configHash'])) for t in raw['targets']), 'bytes':not path.exists() if name=='empty-experimental' else path.read_text()==text}
    if name=='default':checks['defaultOrigin']=len(raw['targets'])==1 and raw['targets'][0]['source']=='default'
    if name=='error':
        path.write_bytes(VALID.encode('utf-8'));recovery=m.command(dest,'recovery',[m.BINARY,'check','--root',work,'--format','json']);report=json.loads(recovery.stdout)
        checks['recovery']=recovery.returncode==0 and report['analysisComplete'] is True and report['diagnostics']==[] and report['notEvaluated']==[] and report['filesAnalyzed']==report['resourcesAnalyzed']==1
    m.result(name,checks)

def lsp_case(name):
    dest,work,path,text,_=prepare(name);client=None;timer=None;checks={};responses={}
    def request(method,p):
        r=client.request(method,p);responses[str(len(responses))]={'method':method,'response':r};return r
    def rename(p,t):return request('textDocument/rename',{'textDocument':{'uri':p.as_uri()},'position':m.pos(t,'name: main',offset=6),'newName':'renamed'})
    def actions(p,t):
        start=m.pos(t,'entrypoint: main',offset=12)
        return request('textDocument/codeAction',{'textDocument':{'uri':p.as_uri()},'range':{'start':start,'end':start}})
    try:
        client=m.LSP(dest,work);timer=threading.Timer(45,lambda:client.p.kill() if client.p.poll() is None else None);timer.start()
        checks['stablePublication']=client.open(path,text)==[]
        response=rename(path,text)
        checks['stableRename']='result' in response and m.apply(text,response['result'],path.as_uri(),1)==text.replace('entrypoint: main','entrypoint: renamed').replace('name: main','name: renamed')
        if name=='pin-mixed':
            cfg=work/'.argo-workflows-ls.yaml';original=cfg.read_text()
            client.send('textDocument/didOpen',{'textDocument':{'uri':cfg.as_uri(),'version':1,'languageId':'yaml','text':original}})
            start=m.pos(original,'name: stable')
            offered=request('textDocument/codeAction',{'textDocument':{'uri':cfg.as_uri()},'range':{'start':start,'end':start}}).get('result',[])
            checks['independentScopePin']=len(offered)==1
            if offered:
                response=request('codeAction/resolve',offered[0]);wanted=original.replace('    include: ["stable/*.yaml"]\n','    include: ["stable/*.yaml"]\n    targetVersion: "3.6.0"\n')
                checks['scopePinBytes']='result' in response and m.apply(original,response['result']['edit'],cfg.as_uri(),1)==wanted
        elif name=='edits':
            peer=work/'preview/case.yaml';checks['experimentalPublication']=client.open(peer,VALID)==[]
            response=rename(peer,VALID);checks['olderTargetRename']='result' in response and m.apply(VALID,response['result'],peer.as_uri(),1)==VALID.replace('entrypoint: main','entrypoint: renamed').replace('name: main','name: renamed')
            peer_typo=VALID.replace('entrypoint:', 'entrypoin:');client.change(peer,peer_typo,2)
            peer_start=m.pos(peer_typo,'entrypoin')
            checks['olderTargetActions']=len(request('textDocument/codeAction',{'textDocument':{'uri':peer.as_uri()},'range':{'start':peer_start,'end':peer_start}}).get('result',[]))==1
            # Known typo yields one exact edit; retain action across a target switch.
            typo=text.replace('entrypoint:', 'entrypoin:');client.change(path,typo,2)
            start=m.pos(typo,'entrypoin')
            offered=request('textDocument/codeAction',{'textDocument':{'uri':path.as_uri()},'range':{'start':start,'end':start}}).get('result',[])
            checks['stableAction']=len(offered)==1
            if offered:
                resolved=request('codeAction/resolve',offered[0]);checks['actionBytes']='result' in resolved and m.apply(typo,resolved['result']['edit'],path.as_uri(),2)==text
            cfg=work/'.argo-workflows-ls.yaml';client.send('textDocument/didOpen',{'textDocument':{'uri':cfg.as_uri(),'version':1,'languageId':'yaml','text':cfg.read_text().replace('targetVersion: "3.6.0"','targetVersion: "3.5.0"')}})
            if offered:checks['staleAction']=request('codeAction/resolve',offered[0]).get('error',{}).get('code')==-32801
        else:
            client.send('workspace/didChangeConfiguration',{'settings':{'targetVersion':'3.5.0'}})
            checks['settingsPublication']=client.diagnostics(path.as_uri(),1)==[]
            checks['settingsRenameSupported']='result' in rename(path,text)
            client.send('workspace/didChangeConfiguration',{'settings':{'targetVersion':'3.6.0'}})
            checks['settingsRecovery']=client.diagnostics(path.as_uri(),1)==[]
            restored=rename(path,text)
            checks['settingsRenameRestored']='result' in restored and m.apply(text,restored['result'],path.as_uri(),1)==text.replace('entrypoint: main','entrypoint: renamed').replace('name: main','name: renamed')
        checks['bytes']=path.read_text()==text
    finally:
        if client:client.close();checks['naturalExit']=client.p.returncode==0
        if timer:timer.cancel()
        m.save(dest/'responses.json',responses)
    m.result(name,checks)

selected=CASES
if '--cases' in sys.argv:
    requested=sys.argv[sys.argv.index('--cases')+1].split(',')
    unknown=sorted(set(requested)-set(CASES))
    if unknown:raise SystemExit('unknown delivery cases: '+', '.join(unknown))
    if len(set(requested))!=len(requested):raise SystemExit('duplicate delivery cases')
    selected=[x for x in CASES if x in requested]
if not selected:m.save(m.OUT/'results.json',{'pass':False,'caseCount':0,'nativeStarted':False});raise SystemExit(1)
if '--prepare' in sys.argv:
    for name in selected:prepare(name)
else:
    identity=m.command(m.OUT,'version',[m.BINARY,'version','--json']);value=json.loads(identity.stdout)
    m.result('identity',{'exit':identity.returncode==0,'ready':value.get('analysisReady') is True,'default':value.get('defaultTargetVersion')=='auto','accepted':value.get('acceptedTargetVersions')==[r['version'] for r in LOCK['versions']]})
    for name in selected:
        try:(lsp_case if name in ['edits','settings','pin-mixed'] else cli_case)(name)
        except Exception:
            m.save(m.OUT/(name+'-fatal.json'),{'error':traceback.format_exc()});raise
m.save(m.OUT/'manifest.json',{'command':sys.argv,'nativeSha256':m.sha(m.BINARY),'consumerSha256':m.sha(Path(__file__)),'helperSha256':m.sha(HERE/'wf-audit-gaps.py'),'caseCount':len(selected),'preparedOnly':'--prepare' in sys.argv})
raise SystemExit(0 if '--prepare' in sys.argv or (len(m.RESULTS)==len(selected)+1 and [r['case'] for r in m.RESULTS]==['identity']+selected and all(r['pass'] for r in m.RESULTS)) else 1)
