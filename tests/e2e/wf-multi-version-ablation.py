#!/usr/bin/env python3
"""Remove one compatibility mechanism at a time in isolated native builds.

No production tree is changed. Observable black-box outcomes decide which small
mechanisms are necessary; the final variant measures the cost of removing $defs.
"""
import copy, hashlib, importlib.util, json, os, pathlib, shutil, subprocess, sys, time
ROOT=pathlib.Path(__file__).resolve().parents[2]
BINARY=pathlib.Path(sys.argv[1]).resolve();OUT=pathlib.Path(sys.argv[2]).resolve();OUT.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('wire',ROOT/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
source=OUT/'source';source.mkdir(exist_ok=False)
for folder in ['cmd','internal','compat','vendor']:shutil.copytree(ROOT/folder,source/folder)
for filename in ['go.mod','go.sum']:shutil.copy2(ROOT/filename,source/filename)
lock=json.loads((source/'compat/catalog.lock.json').read_text())
file=next(x for x in lock['automatic']['sources'] if x['kind']=='Workflow')['normalizedSchemas']['argoproj.io/v1alpha1']['file']
schema_path=source/'internal/assets/schemas'/file
original_schema=schema_path.read_bytes();schema=json.loads(original_schema)
query_path=source/'internal/analysis/query_context.go';original_query=query_path.read_text()
BASE={'apiVersion':'argoproj.io/v1alpha1','kind':'Workflow','metadata':{'name':'ablation'},'spec':{'entrypoint':'main','templates':[{'name':'main','container':{'image':'alpine'}}]}}
OLD=copy.deepcopy(BASE);OLD['spec']['podPriority']=1
BAD=copy.deepcopy(BASE);BAD['spec']['podResources']=True
MIXED=copy.deepcopy(BASE);MIXED['spec'].update({'podPriority':1,'podResources':{'requests':{'cpu':'1'}}})
inputs={}
for name,value in [('old',OLD),('bad-type',BAD),('mixed',MIXED),('base',BASE)]:
    work=OUT/'inputs'/name;work.mkdir(parents=True);(work/'case.yaml').write_text(json.dumps(value)+'\n');inputs[name]=work

def run(binary,name,label):
    dest=OUT/label;dest.mkdir(exist_ok=True);start=time.monotonic();peak=0
    with (dest/(name+'.stdout')).open('wb') as so,(dest/(name+'.stderr')).open('wb') as se:
        p=subprocess.Popen([str(binary),'check','--root',str(inputs[name]),'--format','json'],stdout=so,stderr=se)
        while p.poll() is None:
            if sys.platform.startswith('linux'):
                try:peak=max(peak,int(next(x.split()[1] for x in pathlib.Path('/proc/%d/status'%p.pid).read_text().splitlines() if x.startswith('VmRSS:'))))
                except (OSError,StopIteration):pass
            if time.monotonic()-start>60:p.kill();p.wait();raise RuntimeError(label+' validation exceeded 60 seconds')
            time.sleep(.005)
    report=json.loads((dest/(name+'.stdout')).read_text())
    return {'exit':p.returncode,'complete':report['analysisComplete'],'codes':[d['code'] for d in report['diagnostics']],'notEvaluated':report['notEvaluated'],'seconds':time.monotonic()-start,'peakRSSKiB':peak}

def completion(binary,label):
    dest=OUT/label;dest.mkdir(exist_ok=True);work=dest/'query';work.mkdir(exist_ok=True)
    marked='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: query}\nspec:\n  podPriority: 1\n  podR§\n  templates:\n    - name: main\n      container: {image: alpine}\n'
    text=marked.replace('§','');path=work/'case.yaml';path.write_text(text);prior=m.BINARY;m.BINARY=binary;c=m.LSP(dest,work)
    try:
        c.open(path,text);r=c.request('textDocument/completion',{'textDocument':{'uri':path.as_uri()},'position':m.pos(marked,'§')});m.save(dest/'query-response.json',r)
        assert 'error' not in r,r
        return 'podResources' in [x['label'] for x in r.get('result',[])]
    finally:c.close();m.BINARY=prior

baseline={name:run(BINARY,name,'baseline') for name in inputs}
baseline['incompatibleCompletion']=completion(BINARY,'baseline')
assert all(baseline[name]['complete'] for name in inputs) and baseline['old']['exit']==baseline['base']['exit']==0 and baseline['bad-type']['exit']==baseline['mixed']['exit']==1 and not baseline['incompatibleCompletion'],baseline
m.save(OUT/'baseline.json',baseline)
rows=[]
go=os.environ.get('GO') or (str(pathlib.Path(os.environ['GOROOT'])/'bin/go') if os.environ.get('GOROOT') else shutil.which('go'))
assert go,'Go compiler required for isolated ablation builds'

def loose(node):
    if isinstance(node,dict):return {k:loose(v) for k,v in node.items() if not(k=='additionalProperties' and v is False)}
    if isinstance(node,list):return [loose(v) for v in node]
    return node

def expand(node):
    if isinstance(node,dict):
        if set(node)=={'$ref'}:return expand(schema['$defs'][node['$ref'].split('/')[-1]])
        return {k:expand(v) for k,v in node.items() if k!='$defs'}
    if isinstance(node,list):return [expand(v) for v in node]
    return node

for name in ['latest-only','open-alternatives','no-query-selection','no-schema-interning']:
    dest=OUT/name;dest.mkdir(exist_ok=True);schema_path.write_bytes(original_schema);query_path.write_text(original_query)
    if name=='latest-only':
        changed=copy.deepcopy(schema);changed['anyOf']=changed['anyOf'][:1];schema_path.write_text(json.dumps(changed,separators=(',',':')))
    elif name=='open-alternatives':schema_path.write_text(json.dumps(loose(schema),separators=(',',':')))
    elif name=='no-query-selection':
        before='schema = queryAutomatic(ctx, schema, value, activePath)';assert before in original_query
        query_path.write_text(original_query.replace(before,'_ = activePath // compatibility branch selection removed for ablation'))
    else:schema_path.write_text(json.dumps(expand(schema),separators=(',',':')))
    binary=dest/('server.exe' if os.name=='nt' else 'server')
    cmd=[go,'build','-mod=vendor','-trimpath','-buildvcs=false','-o',str(binary),'./cmd/argo-workflows-ls']
    build=subprocess.run(cmd,cwd=source,env=os.environ|{'GOWORK':'off','GOTOOLCHAIN':'local'},capture_output=True,timeout=180)
    (dest/'build.stdout').write_bytes(build.stdout);(dest/'build.stderr').write_bytes(build.stderr)
    row={'variant':name,'buildExit':build.returncode,'schemaBytes':schema_path.stat().st_size,'baselineSchemaBytes':len(original_schema),'schemaSha256':m.sha(schema_path),'querySha256':m.sha(query_path),'detected':False}
    if build.returncode==0:
        row['binarySha256']=m.sha(binary)
        if name=='latest-only':
            row['old']=run(binary,'old',name);row['detected']=row['old']['complete'] and row['old']['exit']==1
            row['decision']='Keep whole-release alternatives: latest schema alone loses valid old fields.'
        elif name=='open-alternatives':
            row['badType']=run(binary,'bad-type',name);row['mixed']=run(binary,'mixed',name)
            row['detected']=row['badType']['exit']==row['mixed']['exit']==0
            row['decision']='Keep structural closure: otherwise old branches silently accept invalid new fields and incompatible combinations.'
        elif name=='no-query-selection':
            row['incompatibleCompletion']=completion(binary,name);row['detected']=row['incompatibleCompletion']
            row['decision']='Keep query branch selection so completion cannot suggest a field incompatible with the current document.'
        else:
            row['base']=run(binary,'base',name);row['old']=run(binary,'old',name)
            row['semanticParity']=all(row[k]['exit']==0 and row[k]['complete'] for k in ['base','old'])
            row['budgetExhausted']=all(row[k]['exit']==3 and not row[k]['complete'] and not row[k]['codes'] and 'context deadline exceeded' in row[k]['notEvaluated'] for k in ['base','old'])
            row['sizeRatio']=row['schemaBytes']/len(original_schema)
            row['memoryRatio']=row['base']['peakRSSKiB']/baseline['base']['peakRSSKiB'] if baseline['base']['peakRSSKiB'] else None
            row['detected']=(row['semanticParity'] or row['budgetExhausted']) and row['sizeRatio']>3 and (row['memoryRatio'] is None or row['memoryRatio']>1.5)
            row['decision']=('Keep build-time schema interning: the baseline completes, while expansion exceeds the unchanged analysis deadline with no completed result and substantially higher memory.' if row['budgetExhausted'] else 'Keep build-time schema interning: removing it preserves these validation results but substantially increases schema size and memory.')
    rows.append(row);m.save(OUT/'results.json',rows);print(json.dumps(row),flush=True)
# Remove the copied sources and binaries after retaining exact change hashes and evidence.
shutil.rmtree(source)
m.save(OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'catalogSha256':m.sha(ROOT/'compat/catalog.lock.json'),'scope':'isolated native mechanism removal; no production source mutation'})
raise SystemExit(0 if len(rows)==4 and all(r['detected'] for r in rows) else 1)
