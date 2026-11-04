#!/usr/bin/env python3
"""Compile isolated semantic ablations and require observable protocol failures."""
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
OUT=pathlib.Path(sys.argv[1]).resolve(); OUT.mkdir(parents=True,exist_ok=True)
GOROOT=os.environ['GOROOT']; ENV=dict(os.environ,GOWORK='off',CGO_ENABLED='1')
variants=[
 ('environment-closure','internal/analysis/localrename.go','if p[0] == "$env" {','if false && p[0] == "$env" {','env-static-refusal,env-dynamic-refusal,env-get-refusal,env-shadow-refusal,env-alias-refusal'),
 ('expression-let-scope','internal/analysis/expressionreferences.go','if shadowed[node] {','if false && shadowed[node] {','expression-shadowed-input,expression-shadow-initializer'),
 ('step-self-hook','internal/analysis/localrename.go','hookVisible[renameKey{i, "step", stringValue(member(step, "name"))}] = true','// self hook visibility removed','step-self-hook-declaration,step-self-hook-reference'),
 ('expression-syntax','internal/analysis/expressionreferences.go','return nil, err','return memberReferences(text), nil','malformed-expression-tail,malformed-expression-parenthesis,malformed-raw-expression'),
 ('expression-lexical-identity','internal/analysis/expressionreferences.go','return refs, nil','return memberReferences(text), nil','expression-escaped-literal,expression-raw-literal,expression-comment-preserved'),
 ('expression-rune-to-byte','internal/analysis/expressionreferences.go','Start: offsets[loc.From], End: offsets[loc.To]','Start: loc.From, End: loc.To','expression-unicode-before-member'),
 ('dynamic-closure','internal/analysis/localrename.go','if b.blocked[key] {','if false && b.blocked[key] {','computed-index-target-refused,dynamic-index'),
 ('call-input-binding','internal/analysis/localrename.go','b.reference(callee, "inputs."+kind, member(arg, "name"), true)','_ = arg','input-parameter-call-argument'),
 ('depends-binding','internal/analysis/localrename.go','b.taskNames(i, depends, true)','_ = depends','task-declaration'),
 ('scope-simulation','internal/lsp/edits.go','if err := analysis.SafeScopePlan(ctx, w, d, plan); err != nil {','if err := analysis.SafeScopePlan(ctx, w, d, plan); false && err != nil {','incomplete-same-scope'),
 ('reference-set','internal/analysis/localrename.go','if len(after.symbols[nextKey]) != len(b.symbols[key]) {','if false && len(after.symbols[nextKey]) != len(b.symbols[key]) {','workflow-reserved-json'),
]
results=[]
def run(dest,label,args,**kwargs):
 r=subprocess.run(list(map(str,args)),capture_output=True,**kwargs)
 (dest/(label+'.stdout')).write_bytes(r.stdout); (dest/(label+'.stderr')).write_bytes(r.stderr)
 (dest/(label+'.command.json')).write_text(json.dumps({'argv':list(map(str,args)),'exitCode':r.returncode},indent=2)+'\n')
 return r
for name,path,before,after,cases in variants:
 dest=OUT/name; dest.mkdir(exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='wf-rename-ablation-') as tmp:
  copy=pathlib.Path(tmp)
  for folder in ['cmd','internal','compat','schemas','vendor']: shutil.copytree(ROOT/folder,copy/folder)
  for file in ['go.mod','go.sum']: shutil.copy2(ROOT/file,copy/file)
  f=copy/path; text=f.read_text(); assert text.count(before)>=1,(name,text.count(before)); f.write_text(text.replace(before,after))
  (dest/'mutation.json').write_text(json.dumps({'file':path,'before':before,'after':after,'cases':cases},indent=2)+'\n')
  binary=dest/'server'
  build=run(dest,'build',[GOROOT+'/bin/go','build','-mod=vendor','-trimpath','-buildvcs=false','-o',binary,'./cmd/argo-workflows-ls'],cwd=copy,env=ENV)
  item={'variant':name,'compiled':build.returncode==0,'detected':False}
  if build.returncode==0:
   check=run(dest,'e2e',[sys.executable,ROOT/'tests/e2e/wf-local-rename.py',binary,dest/'e2e'],env=dict(ENV,WF_RENAME_CASES=cases))
   observed=json.loads((dest/'e2e/results.json').read_text())
   item.update(binarySha256=hashlib.sha256(binary.read_bytes()).hexdigest(),detected=check.returncode==1 and len(observed)==len(cases.split(',')) and all(x['case']!='runner-error' for x in observed) and any(not x['pass'] for x in observed))
  results.append(item); (OUT/'results.json').write_text(json.dumps(results,indent=2)+'\n'); print(item,flush=True)
sys.exit(0 if all(x['compiled'] and x['detected'] for x in results) else 1)
