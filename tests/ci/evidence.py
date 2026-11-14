"""Read-only revalidation of original native executions and child contracts."""
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import run
from control import ROOT, identity, require, sha, git

RESOURCES = {
'adversarial': [('wf-bounded-aggregate-input',512),('wf-bounded-expressions',512),('wf-bounded-expression-lsp',512),('wf-bounded-process',512),('wf-bounded-render-deadline',512),('wf-bounded-snapshot-budget-refusal',512),('wf-bounded-snapshot-budget-exact',1024),('wf-semantic-origin-budget',512)],
'load': [('workload-generator',0), *[(name,8192) for name in ('wf-bounded-full','wf-bounded-cancel','wf-bounded-lsp-cold','wf-bounded-lsp-load','wf-bounded-active-latency','wf-bounded-tool-scan','wf-schema-query-load','wf-domain-external-load')]],
'queries': [('workload-generator',0), *[(name,8192) for name in ('wf-bounded-lsp-probe','wf-external-candidate-density','wf-inherited-parameter-density','wf-exit-query-density','wf-remediation-load','wf-remediation-retention','wf-query-handoff-retention')]],
'index':[(f'persistent-{count}-{sample}',8192) for count in (10000,50000) for sample in range(5)]}

def load(path):return json.loads(path.read_text())
def command(path):
 value=load(path/'command.json')
 require(value.get('executionIdentity')==identity(),'stale or unstamped original command')
 require(value.get('started') is True and value.get('timedOut') is False and value.get('exitCode')==0 and value.get('pass') is True,'command did not complete successfully')
 for stream in ('stdout','stderr'):require(value.get(stream+'Sha256')==sha(path/(stream+'.log')),'missing or changed raw command output')
 return value

def child_streams(destination):
 # The command receipts declare which streams must exist, including empty stderr.
 # Listing only surviving streams would let deletion manufacture complete evidence.
 records=list(destination.rglob('*.command.json'))
 require(records,'missing child command receipts')
 for record in records:
  label=record.name.removesuffix('.command.json')
  require(label in ('check','version','lsp'),'unknown child stream contract')
  suffix='.raw' if label=='lsp' else ''
  for stream in ('stdout','stderr'):
   require((record.parent/(label+'.'+stream+suffix)).is_file(),'missing required raw child stream: '+str(record.parent/(label+'.'+stream+suffix)))

def build(root,lane):
 value=load(root/'build/build.json')
 require(value.get('schemaVersion')==1 and value.get('buildRole')=='ordinary','missing ordinary build receipt')
 require(value.get('sourceCommit')==identity()['testedSHA'] and value.get('dirty') is False,'wrong build source')
 from build_identity import input_digest
 expected_platform=lane.removeprefix('native-').replace('-','/') if lane.startswith('native-') else 'linux/amd64'
 require(value.get('platform')==expected_platform,'wrong native platform')
 require(value.get('sourceTree')==git('rev-parse','HEAD^{tree}') and value.get('inputs')==input_digest(),'wrong source tree or build inputs')
 require(all(value.get('producer',{}).get(k)==v for k,v in identity().items()),'stale producer execution')
 binary=root/'build'/('argo-workflows-ls.exe' if lane=='native-windows-amd64' else 'argo-workflows-ls')
 require(sha(binary)==value.get('binarySha256'),'build binary bytes missing or changed')
 require(value.get('identity',{}).get('sourceCommit')==identity()['testedSHA'] and value.get('identity',{}).get('buildMode')=='release','wrong native build identity')
 return binary

def native(lane,root):
 group=lane.split('-')[0];folder=root/group;summary=load(folder/'summary.json')
 producer_platform=lane.removeprefix('native-').replace('-','/') if lane.startswith('native-') else 'linux/amd64'
 require(summary.get('platform')==producer_platform,'summary platform differs from trusted lane')
 require(summary.get('identity')==identity() and summary.get('runnerSha256')==sha(ROOT/'tests/ci/run.py'),'stale summary identity')
 rows=summary.get('tests',[]);require(summary.get('pass') is True and rows and all(r.get('pass') is True for r in rows),'empty or failed summary')
 if group in ('native','extended'):
  binary=build(root,lane);names=list(run.NATIVE if group=='native' else run.EXTENDED[int(lane.rsplit('-',1)[1])::3]);require([r.get('name') for r in rows]==names,'incorrect suite selection')
  for row,name in zip(rows,names):
   if name=='wf-multi-version':
    child_streams(folder/name)
    path_type=PureWindowsPath if producer_platform.startswith('windows/') else PurePosixPath
    original_binary=load(root/'build/build.json')['binary'];original_destination=None
    require(path_type(original_binary).is_absolute(),'original binary path is not absolute')
    for n in range(run.MULTI_VERSION_SHARDS):
     outer=command(folder/'logs'/f'wf-multi-version-shard-{n}');argv=outer.get('command',[])
     require(len(argv)==6 and argv[2]==original_binary and argv[4:]==['--release-shard',f'{n}/{run.MULTI_VERSION_SHARDS}'],'wrong original matrix execution')
     shard_destination=path_type(argv[3])
     require(shard_destination.is_absolute() and shard_destination.name==f'shard-{n}','wrong original shard path')
     if original_destination is None:original_destination=shard_destination.parent
     require(shard_destination.parent==original_destination,'mixed original shard executions')
    count=run.verify_multi_version_shards(folder/name,binary,write_measurements=False,execution_binary=original_binary,execution_destination=original_destination)['caseCount']
   else:
    command(folder/'logs'/name);count=run.consumer_results(name,folder/name,binary,producer_platform=producer_platform)
   require(count>0 and row.get('caseCount')==count,'incorrect actual child case count')
 elif group=='resources':
  import resources
  binary=build(root,lane);selection=RESOURCES[lane.removeprefix('resources-')];require([r.get('name') for r in rows]==[n for n,_ in selection],'incorrect resource selection')
  for row,(name,memory) in zip(rows,selection):
   command(folder/'logs'/('workload' if name=='workload-generator' else name))
   if not memory:continue
   destination=folder/name;resources.verify_resource_receipt(destination,memory)
   script=name;contract=name;filename='results.json';pass_key='pass';assertion_key='assertions'
   if name.startswith('persistent-'):
    script='wf-persistent-workspace';contract=script+('-50000' if name.startswith('persistent-50000-') else '');assertion_key='checks'
   elif name.startswith('wf-bounded-snapshot-budget-'):script='wf-bounded-snapshot-budget'
   elif name=='wf-bounded-cancel':filename='report.json';pass_key='passed'
   run.verify_consumer_identity(script,destination,binary)
   count=run.validate_results(destination/filename,pass_key=pass_key,assertion_key=assertion_key)
   run.verify_result_contract(contract,destination/filename,producer_platform=producer_platform)
   require(count>0 and row.get('caseCount')==count,'incorrect resource child count')
 elif group=='mutations':
  selected=lane.removeprefix('mutations-');expected=[selected+' mutations']
  if selected=='audit':expected+=['shared-yaml-projection mutations','catalog-queries mutations','responsive-editing mutations','shared-lifecycle mutations']
  require([r.get('name') for r in rows]==expected,'incorrect mutation selection')
  command(folder/'driver');count=run.verify_mutation_contract(selected,load(folder/'gate/results.json'))
  require(rows[0].get('caseCount')==count and count>0,'incorrect mutation count')
  if selected=='audit':
   for n,count in [('shared-yaml-projection',2),('catalog-queries',7),('responsive-editing',4)]:
    command(folder/(n+'-driver'));report=load(folder/n/('results.json' if n=='shared-yaml-projection' else 'report.json'))
    if n=='shared-yaml-projection':require(len(report)==count and all(r.get('buildExit')==0 and r.get('consumerExit')==1 and r.get('detected') is True for r in report),'missing projection mutation evidence')
    else:require(report.get('passed') is True and len(report.get('rows',[]))==count+1 and all(r.get('passed') is True for r in report['rows']),'missing semantic mutation evidence')
   command(folder/'shared-lifecycle-driver');report=load(folder/'shared-lifecycle/results.json');require(report.get('positive') is True and len(report.get('results',[]))==7 and all(r.get('compiled') is True and r.get('detected') is True for r in report['results']),'missing lifecycle mutation evidence')
