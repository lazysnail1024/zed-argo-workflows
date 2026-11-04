#!/usr/bin/env python3
"""CLI scope ownership and validation across literal-prefix/glob boundaries."""
from pathlib import Path
import sys,json,importlib.util
root=Path(__file__).resolve().parents[2];spec=importlib.util.spec_from_file_location('wire',root/'tests/e2e/wf-audit-gaps.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: scoped}\nspec:\n  parallelism: wrong\n  templates: [{name: main, container: {image: alpine}}]\n'
cases=[
 ('recursive-zero-and-many','alpha/**/*.yaml',['alpha/a.yaml','alpha/deep/b.yaml','alphabet/c.yaml','alpha.yaml'],['alpha/a.yaml','alpha/deep/b.yaml'],[]),
 ('leading-recursive','**/*.yaml',['a.yaml','deep/b.yaml','deep/more/c.yaml'],['a.yaml','deep/b.yaml','deep/more/c.yaml'],[]),
 ('leading-class','[ab]*/file.yaml',['alpha/file.yaml','beta/file.yaml','gamma/file.yaml'],['alpha/file.yaml','beta/file.yaml'],[]),
 ('leading-question','?eta/*.yaml',['beta/a.yaml','zeta/b.yaml','eta/c.yaml'],['beta/a.yaml','zeta/b.yaml'],[]),
 ('class-after-prefix','alpha/[ab].yaml',['alpha/a.yaml','alpha/b.yaml','alpha/c.yaml'],['alpha/a.yaml','alpha/b.yaml'],[]),
 ('question-boundary','alpha/?.yaml',['alpha/a.yaml','alpha/ab.yaml'],['alpha/a.yaml'],[]),
 ('star-boundary','alpha/pre*.yaml',['alpha/pre.yaml','alpha/prefix.yaml','alpha/pre/deep.yaml','alpha/other.yaml'],['alpha/pre.yaml','alpha/prefix.yaml'],[]),
 ('literal-name','alpha/a.yaml',['alpha/a.yaml','alpha/a.yaml2.yaml','alphabet/a.yaml'],['alpha/a.yaml'],[]),
 ('exclude-boundary','alpha/**/*.yaml',['alpha/a.yaml','alpha/skip1.yaml','alpha/skip/deep.yaml'],['alpha/a.yaml','alpha/skip/deep.yaml'],['alpha/skip*.yaml']),
]
for name,pattern,files,wanted,exclude in cases:
 config=m.CONFIG+'exclude: '+json.dumps(exclude)+'\nscopes:\n  - name: selected\n    include: '+json.dumps([pattern])+'\n'
 dest,work=m.setup(name,{p:text for p in files},config);expected=[{'uri':(work/p).as_uri(),'range':{'start':{'line':4,'character':15},'end':{'line':4,'character':20}},'code':'SCHEMA_TYPE','severity':'Error','source':'argo-workflows','positionKind':'exact'} for p in wanted];m.save(dest/'expected.json',{'diagnostics':expected,'files':len(wanted),'resources':len(wanted),'exitCode':2})
 run=m.command(dest,'check',[m.BINARY,'check','--root',work,'--scope','selected','--format','json']);report=json.loads(run.stdout);actual=[{k:d[k] for k in ['uri','range','code','severity','source','positionKind']} for d in report['diagnostics']];norm=lambda v:sorted(json.dumps(x,sort_keys=True) for x in v);checks={'exit':run.returncode==int(any(d['severity']=='Error' for d in expected)),'exactDiagnostics':norm(actual)==norm(expected),'counts':report['filesAnalyzed']==len(wanted) and report['resourcesAnalyzed']==len(wanted),'noConfigurationErrors':report['configurationErrors']==[],'complete':report['analysisComplete'] is True,'selectedTarget':all(t['scope']=='selected' for t in report['targets'])};m.result(name,checks,report)
for name,extra,files,fragment in [
 ('nonselected-overlap','  - name: other1\n    include: ["beta/**/*.yaml"]\n  - name: other2\n    include: ["beta/*.yaml"]\n',['alpha/a.yaml','beta/a.yaml'],'multiple scopes'),
 ('selected-overlap','  - name: other\n    include: ["**/*.yaml"]\n',['alpha/a.yaml'],'multiple scopes'),
 ('nonselected-invalid-recursion','  - name: other\n    include: ["missing/**x/*.yaml"]\n',['alpha/a.yaml'],'**'),
 ('nonselected-invalid-class','  - name: other\n    include: ["missing/[.yaml"]\n',['alpha/a.yaml'],'syntax'),
 ('nonselected-invalid-backslash','  - name: other\n    include: '+json.dumps(['missing\\*.yaml'])+'\n',['alpha/a.yaml'],'glob'),
]:
 config=m.CONFIG+'scopes:\n  - name: selected\n    include: ["alpha/**/*.yaml"]\n'+extra;dest,work=m.setup(name,{p:text for p in files},config);m.save(dest/'expected.json',{'configurationErrorCount':1,'messageContains':fragment,'diagnostics':[],'files':0,'resources':0,'exitCode':2});run=m.command(dest,'check',[m.BINARY,'check','--root',work,'--scope','selected','--format','json']);report=json.loads(run.stdout);errors=report['configurationErrors'];m.result(name,{'exit':run.returncode==2,'configurationRejected':len(errors)==1 and fragment in errors[0],'noPartialAnalysis':report['diagnostics']==[] and report['filesAnalyzed']==0 and report['resourcesAnalyzed']==0,'incomplete':report['analysisComplete'] is False},report)
m.save(m.OUT/'manifest.json',{'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(Path(__file__)),'caseCount':len(m.RESULTS),'inputHashes':{str(p.relative_to(m.OUT)):m.sha(p) for p in m.OUT.rglob('*.yaml')}});sys.exit(0 if len(m.RESULTS)==14 and all(x['pass'] for x in m.RESULTS) else 1)
