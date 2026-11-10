#!/usr/bin/env python3
import json,pathlib,tempfile,subprocess,sys,hashlib,shutil
root=pathlib.Path(__file__).resolve().parents[2];binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]) if len(sys.argv)>2 else root/'artifacts/wf3-workspace';out.mkdir(parents=True,exist_ok=True)
e=json.loads((root/'tests/e2e/wf3-workspace.expected.json').read_text());results=[]
config='schemaVersion: 1\ntargetVersion: "3.6.0"\nscopes:\n  - name: dev\n    include: ["dev/**/*.yaml"]\n    targetVersion: "3.6.0"\n  - name: prod\n    include: ["prod/**/*.yaml"]\n    targetVersion: "3.5.0"\n'
with tempfile.TemporaryDirectory(prefix='wf-workspace-') as d:
 work=pathlib.Path(d)
 for name in ['dev/a.yaml','dev/b.yaml','prod/c.yaml']:
  p=work/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text((root/'tests/fixtures/schema/mixed.yaml').read_text())
 cfg=work/'.argo-workflows-ls.yaml';cfg.write_text(config)
 def run(name,args,check):
  cmd=[str(binary),'check','--root',str(work),'--format','json',*args];p=subprocess.run(cmd,capture_output=True,timeout=20);(out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr)
  assertions={'exit':p.returncode==(1 if name in ['scopes','override','file'] else e['exitCode'])}
  try:assertions.update(check(json.loads(p.stdout)))
  except Exception as err:assertions['parse']=str(err)
  results.append({'name':name,'command':cmd,'exitCode':p.returncode,'assertions':assertions,'pass':all(x is True for x in assertions.values())})
 cmd=[str(binary),'explain-config','--root',str(work)];p=subprocess.run(cmd,capture_output=True,timeout=10);(out/'explain.stdout').write_bytes(p.stdout);(out/'explain.stderr').write_bytes(p.stderr)
 try:v=json.loads(p.stdout);passed=p.returncode==0 and {t['scope']:t['version'] for t in v['targets']}==e['scopeVersions'] and v['analysisReady'] is True
 except Exception:passed=False
 results.append({'name':'explain-config','command':cmd,'exitCode':p.returncode,'assertions':{'effectiveTargets':passed},'pass':passed})
 run('scopes',[],lambda v:{'versions':{t['scope']:t['version'] for t in v['targets']}==e['scopeVersions'],'binding':all(d['targetId'] in {t['id'] for t in v['targets']} for d in v['diagnostics']),'noConfigError':not v['configurationErrors'],'analysisComplete':v['analysisComplete'] is True,'supportedTargets':all(t['experimental'] is False and t['analysisComplete'] is True for t in v['targets'])})
 run('override',['--target-version',e['overrideVersion']],lambda v:{'override':len(v['targets'])==2 and all(t['version']==e['overrideVersion'] and t['source']=='cli' for t in v['targets'])})
 run('file',['--scope','dev','--file',str(work/'dev/a.yaml')],lambda v:{'dependencyInput':v['filesAnalyzed']==e['selectedScopeFiles'],'onlyFile':len(v['diagnostics'])==e['selectedDiagnostics'] and all(x['uri'].endswith('/dev/a.yaml') for x in v['diagnostics'])})
 for name,text,args in [('unknownField',config+'typo: true\n',[]),('overlap',config.replace('prod/**/*.yaml','dev/**/*.yaml'),[]),('unknownVersion',config.replace('3.5.0','99.0.0'),[]),('duplicateScope',config.replace('name: prod','name: dev'),[]),('missingScope',config,['--scope','missing']),('outsideFile',config,['--scope','dev','--file',str(work/'prod/c.yaml')])]:
  cfg.write_text(text);run(name,args,lambda v:{'reject':bool(v['configurationErrors']),'incomplete':v['analysisComplete'] is False})
 (out/'input-config.yaml').write_text(config);(out/'input.yaml').write_bytes((root/'tests/fixtures/schema/mixed.yaml').read_bytes())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
(out/'manifest.json').write_text(json.dumps({'command':sys.argv,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'expectedSha256':sha(root/'tests/e2e/wf3-workspace.expected.json'),'inputHashes':{p.name:sha(p) for p in out.glob('input*')}},indent=2));(out/'results.json').write_text(json.dumps(results,indent=2));sys.exit(0 if results and all(r['pass'] for r in results) else 1)
