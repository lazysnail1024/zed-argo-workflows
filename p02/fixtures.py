#!/usr/bin/env python3
"""Explicit synthetic shapes for production gate CLI diagnostics, never product evidence."""
import hashlib,json,pathlib,subprocess,sys
ROOT=pathlib.Path.cwd();sys.path.insert(0,str(ROOT/'tests/ci'))
import control,run,evidence
from build_identity import input_digest
MARKER=b'SYNTHETIC P02 FIXTURE ONLY: NOT AN EXECUTABLE OR PRODUCT RUN\n'
def save(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2)+'\n')
def raw(path,data=MARKER):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_text())
def command(folder,ident,argv=None):
 raw(folder/'stdout.log');raw(folder/'stderr.log',b'');save(folder/'command.json',{'diagnosticFixture':True,'executionIdentity':ident,'started':True,'timedOut':False,'exitCode':0,'pass':True,'command':argv or ['SYNTHETIC-NOT-EXECUTED'],'stdoutSha256':sha(folder/'stdout.log'),'stderrSha256':sha(folder/'stderr.log')})
def manifest(folder,name,binary):
 value={'diagnosticFixture':True,'binarySha256':sha(binary),'testSha256':sha(ROOT/'tests/e2e'/f'{name}.py'),'helperSha256':sha(ROOT/'tests/e2e/support/session.py'),'windowsJobSha256':sha(ROOT/'tests/e2e/support/windows_job.py')}
 if name in ('wf-external-candidate-density','wf-inherited-parameter-density','wf-exit-query-density','wf-query-handoff-retention'):
  value['helperSha256']=sha(ROOT/'tests/e2e/wf-audit-gaps.py');value['queryReadinessHelperSha256']=sha(ROOT/'tests/e2e/query_density_support.py')
 save(folder/'manifest.json',value);return value
CONTRACTS=load(ROOT/'tests/ci/contracts.json')['consumers'];CATALOG=load(ROOT/'compat/catalog.lock.json')
def rows_for(name,platform):
 spec=CONTRACTS[name]
 if spec.get('selection')=='catalog-assets':
  ids=[{'assertion':v} for v in ('every final version tag in the latest two majors exactly once','automatic default','all official CRD kinds')]+[{'version':v['version'],'assertion':'source hash '+s['path']} for v in CATALOG['versions'] for s in v['sources']]+[{'version':v,'assertion':'CronWorkflow schedules availability'} for v in ('3.5.0','3.6.0','4.1.6')]
  return [i|{'pass':True,'assertions':{i['assertion']:True},'diagnosticFixture':True} for i in ids]
 if 'identities' in spec:return [i|{'pass':True,'passed':True,'assertions':{'synthetic':True},'diagnosticFixture':True} for i in spec['identities']]
 omitted=spec.get('linuxAssertions',[]) if not platform.startswith('linux/') else []
 return [r['id']|{'pass':True,'passed':True,spec['assertionKey']:{k:True for k in r['assertions'] if k not in omitted},'diagnosticFixture':True} for r in spec['rows']]
def consumer(folder,name,binary,platform,contract=None,filename='results.json'):
 m=manifest(folder,name,binary)
 if name in run.AGGREGATED:
  key=run.AGGREGATED[name];expected=['3.6.0'] if key=='version' else run.AGGREGATED_CASES[name];m['expectedTargets' if key=='version' else 'expectedCases']=expected;save(folder/'manifest.json',m);count=0
  for v in expected:
   r={'pass':True,'checks':{'synthetic':True},'diagnosticFixture':True};count+=1
   if key=='version':
    n=run.AGGREGATED_VERSION_COUNTS[name];r['caseCount']=n;save(folder/v/'case-results.json',[{'case':str(i),'pass':True,'checks':{'synthetic':True}} for i in range(n)]);count+=n
   save(folder/v/'result.json',r)
  save(folder/'results.json',[{key:v,'exitCode':0,'command':['SYNTHETIC-NOT-EXECUTED']} for v in expected]);return count
 rows=rows_for(contract or name,platform);save(folder/filename,rows);return len(rows)
def matrix(folder,binary,platform,ident,logs):
 count=0;original=str(binary.resolve()) if not platform.startswith('windows') else 'C:\\synthetic\\argo-workflows-ls.exe';destination=str(folder.resolve()) if not platform.startswith('windows') else 'C:\\synthetic\\matrix'
 Path=pathlib.PureWindowsPath if platform.startswith('windows') else pathlib.PurePosixPath
 for n in range(4):
  d=folder/f'shard-{n}';selected=CATALOG['versions'][n::4];pairs=sum(len(v['sources']) for v in selected);command(logs/f'wf-multi-version-shard-{n}',ident,['python','SYNTHETIC.py',original,str(Path(destination)/f'shard-{n}'),'--release-shard',f'{n}/4'])
  save(d/'manifest.json',{'diagnosticFixture':True,'binarySha256':sha(binary),'testSha256':sha(ROOT/'tests/e2e/wf-multi-version.py'),'catalogSha256':sha(ROOT/'compat/catalog.lock.json'),'releaseShard':f'{n}/4','selectedVersions':[v['version'] for v in selected],'totalReleaseCount':146,'releaseCount':len(selected),'celRulesExercised':10 if n==3 else 0,'resourceVersionPairs':pairs})
  rows=[{'case':'release-provenance','pass':True,'assertions':{str(i):True for i in range(447)}},{'case':'machine-identity','pass':True,'assertions':{str(i):True for i in range(5)}},{'case':'every-release-consumer','pass':True,'assertions':{'exactShardOrder':True,'everyReleaseAccepted':True}}]
  if n==3:rows += [{'case':name,'pass':True,'assertions':{'synthetic':True}} for name in ['all-kinds-automatic-session','unavailable-WorkflowTaskSet-hover']+[f'synthetic-{i}' for i in range(72)]]
  save(d/'results.json',rows);count+=len(rows);releases=[]
  for v in selected:
   version=v['version'];kinds=[s['kind'] for s in v['sources']];e=d/('release-'+version)
   releases.append({'version':version,'kinds':kinds,'pass':True,'checks':{str(i):True for i in range(10)},'fixtureSeconds':0.1,'cliWallSeconds':0.1,'processAndEvidenceOverheadSeconds':0.1,'analysisMilliseconds':1})
   save(e/'check.command.json',{'argv':[original,'check','--root',str(Path(destination)/f'shard-{n}'/f'release-{version}'/'input'),'--format','json'],'exitCode':0,'timedOut':False,'diagnosticFixture':True})
   raw(e/'check.stderr',b'');save(e/'check.stdout',{'diagnosticFixture':True,'analysisComplete':True,'diagnostics':[],'notEvaluated':[],'configurationErrors':[],'filesAnalyzed':1,'resourcesAnalyzed':len(kinds),'targets':[{'version':version,'experimental':False}],'elapsedMilliseconds':1})
  save(d/'releases.json',releases)
 return count,original
def native(lane,folder,ident):
 group=lane.split('-')[0];platform=lane.removeprefix('native-').replace('-','/') if group=='native' else 'linux/amd64';sub=folder/group;rows=[]
 binary=folder/'build'/('argo-workflows-ls.exe' if platform=='windows/amd64' else 'argo-workflows-ls');raw(binary);original=str(binary.resolve())
 if group in ('native','extended'):
  names=run.NATIVE if group=='native' else run.EXTENDED[int(lane.rsplit('-',1)[1])::3]
  for name in names:
   if name=='wf-multi-version':count,original=matrix(sub/name,binary,platform,ident,sub/'logs')
   else:count=consumer(sub/name,name,binary,platform);command(sub/'logs'/name,ident)
   rows.append({'name':name,'pass':True,'caseCount':count})
 elif group=='resources':
  for name,memory in evidence.RESOURCES[lane.removeprefix('resources-')]:
   command(sub/'logs'/('workload' if not memory else name),ident)
   if not memory:count=1
   else:
    script=contract=name;filename='results.json'
    if name.startswith('persistent-'):script='wf-persistent-workspace';contract=script+('-50000' if name.startswith('persistent-50000-') else '')
    elif name.startswith('wf-bounded-snapshot-budget-'):script='wf-bounded-snapshot-budget'
    elif name=='wf-bounded-cancel':filename='report.json'
    count=consumer(sub/name,script,binary,platform,contract,filename)
    limits={'cgroup':'synthetic-not-executed','memory.max':str(memory*1024**2),'memory.swap.max':'0','cpu.max':'400000 100000','affinity':[0,1,2,3],'memory.events':'oom 0\noom_kill 0'}
    save(sub/name/'limits.json',{'diagnosticFixture':True,'before':limits,'after':limits,'pass':True,'exitCode':0})
   rows.append({'name':name,'pass':True,'caseCount':count})
 else:
  selected=lane.removeprefix('mutations-');names=run.mutation_selection(selected);command(sub/'driver',ident);save(sub/'gate/results.json',{'diagnosticFixture':True,'selected':names,'variants':[{'variant':n,'status':'killed','detected':True} for n in names],'pass':True});rows=[{'name':selected+' mutations','pass':True,'caseCount':len(names)}]
  if selected=='audit':
   for name,n in [('shared-yaml-projection',2),('catalog-queries',7),('responsive-editing',4),('shared-lifecycle',7)]:
    command(sub/(name+'-driver'),ident);rows.append({'name':name+' mutations','pass':True,'caseCount':n})
    if name=='shared-yaml-projection':save(sub/name/'results.json',[{'buildExit':0,'consumerExit':1,'detected':True} for _ in range(n)])
    elif name=='shared-lifecycle':save(sub/name/'results.json',{'positive':True,'results':[{'compiled':True,'detected':True} for _ in range(n)]})
    else:save(sub/name/'report.json',{'passed':True,'rows':[{'passed':True} for _ in range(n+1)]})
 save(folder/'build/build.json',{'diagnosticFixture':True,'schemaVersion':1,'buildRole':'ordinary','sourceCommit':ident['testedSHA'],'sourceTree':control.git('rev-parse','HEAD^{tree}'),'dirty':False,'platform':platform,'inputs':input_digest(),'producer':ident,'binary':original,'binarySha256':sha(binary),'identity':{'sourceCommit':ident['testedSHA'],'buildMode':'release'}})
 save(sub/'summary.json',{'diagnosticFixture':True,'identity':ident,'runnerSha256':sha(ROOT/'tests/ci/run.py'),'platform':platform,'tests':rows,'pass':True})
def simple(workflow,lane,folder,ident):
 if workflow=='zed':
  if lane.startswith('rust-'):raw(folder/'tests.log',b'SYNTHETIC P02 SHAPE ONLY\ntest result: ok. 1 passed; 0 failed\n')
  else:save(folder/'zed-adapter.json',{'diagnosticFixture':True,'sourceCommit':ident['testedSHA'],'kind':'zed-adapter'})
 else:
  cases=control.INSTALL_CASES.copy()
  if lane.endswith('ubuntu-24.04'):cases['linux-home-default']=True
  if lane.endswith('windows-2025'):cases['powershell-7-compatibility']=True
  save(folder/'report.json',{'diagnosticFixture':True,'executionIdentity':ident,'testSha256':sha(ROOT/'editors/zed/tests/install_e2e.py'),'installerSha256':sha(ROOT/'editors/zed'/('install.ps1' if lane.endswith('windows-2025') else 'install.sh')),'passed':True,'installerDefaultsMatchSource':True,'cases':[{'case':n,'expectedSuccess':v,'exitCode':0 if v else 1} for n,v in cases.items()]})
