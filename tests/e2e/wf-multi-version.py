#!/usr/bin/env python3
"""Independent CLI/LSP acceptance of automatic compatibility and locked releases.

Checks schema editing contracts, not controller execution or admission on a cluster.
"""
import concurrent.futures, copy, importlib.util, json, pathlib, re, sys, time
HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('wire', HERE / 'wf-audit-gaps.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
ROOT = HERE.parents[1]
LOCK = json.loads((ROOT / 'compat/catalog.lock.json').read_text())
VERSIONS = [v['version'] for v in LOCK['versions']]
KINDS = {'Workflow', 'WorkflowTemplate', 'ClusterWorkflowTemplate', 'CronWorkflow', 'WorkflowEventBinding',
         'WorkflowArtifactGCTask', 'WorkflowTaskResult', 'WorkflowTaskSet'}
INTRODUCED = {'WorkflowArtifactGCTask':'3.4.0', 'WorkflowTaskResult':'3.3.0', 'WorkflowTaskSet':'3.2.0'}
def version_tuple(version): return tuple(map(int, version.split('.')))
BASE = {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Workflow', 'metadata': {'name': 'compatibility'}, 'spec': {'entrypoint': 'main', 'templates': [{'name': 'main', 'container': {'image': 'alpine'}}]}}

def fixture(name, value, version=None):
    text = json.dumps(value, indent=2) + '\n'
    dest, work = m.setup(name, {'case.yaml': text}, '' if version is None else 'schemaVersion: 1\ntargetVersion: "'+version+'"\n')
    if version is None: (work / '.argo-workflows-ls.yaml').unlink()
    return dest, work, text

def check(name, value, version=None, error=None):
    dest, work, text = fixture(name, value, version)
    code, report = m.cli(dest, work)
    diagnostics = report.get('diagnostics', [])
    checks = {'completed': report.get('analysisComplete') is True and report.get('notEvaluated') == [] and report.get('configurationErrors') == [],
              'oneResource': report.get('filesAnalyzed') == report.get('resourcesAnalyzed') == 1,
              'target': len(report.get('targets', [])) == 1 and report['targets'][0]['version'] == (version or 'auto'),
              'zeroConfig': version is not None or not (work / '.argo-workflows-ls.yaml').exists(),
              'expectedDiagnostics': (code == 0 and diagnostics == []) if error is None else code == 1 and any(d['code'] == error for d in diagnostics)}
    m.result(name, checks, {'exitCode': code, 'report': report})
    return report

# Independently compare exact tags with the locked upstream Git inventory.
tags={line.split()[1].removeprefix('refs/tags/') for line in (ROOT/'compat/upstream/tags.txt').read_text().splitlines()}
final_tags=[tag for tag in tags if re.fullmatch(r'v\d+\.\d+\.\d+',tag)]
latest=max(tuple(map(int,tag[1:].split('.'))) for tag in final_tags)
expected=sorted((tag[1:] for tag in final_tags if int(tag[1:].split('.')[0]) in [latest[0]-1,latest[0]]),key=lambda s:tuple(map(int,s.split('.'))))
provenance = {'everyFinalVersionTag': VERSIONS == expected, 'exactKnownRange': VERSIONS[0] == '3.0.0' and VERSIONS[-1] == '4.1.6',
              'allKinds': set(LOCK['supportedKinds']) == KINDS and {s['kind'] for s in LOCK['automatic']['sources']} == KINDS,
              'sparseKindInventory': all({s['kind'] for s in v['sources']} == {kind for kind in KINDS if version_tuple(v['version']) >= version_tuple(INTRODUCED.get(kind,'3.0.0'))} for v in LOCK['versions']),
              'automaticDefault': LOCK['defaultVersion'] == 'auto'}
for src in LOCK['automatic']['sources']:
    provenance['availability:'+src['kind']] = src['availableVersions'] == [v['version'] for v in LOCK['versions'] if any(s['kind']==src['kind'] for s in v['sources'])]
for v in LOCK['versions']:
    present={src['kind'] for src in v['sources']}; absent={src['kind'] for src in v['absentSources']}
    provenance['inventory:'+v['version']] = not present & absent and present | absent == KINDS
    for src in v['absentSources']:
        provenance['absence:'+v['version']+':'+src['kind']] = len(src['checks'])==2 and all(x['status']==404 and x['url']=='https://raw.githubusercontent.com/argoproj/argo-workflows/'+v['upstreamCommit']+'/'+x['upstreamPath'] for x in src['checks'])
    for src in v['sources']:
        provenance['raw:'+src['sha256']] = m.sha(ROOT/src['path']) == src['sha256']
        for schema in src['normalizedSchemas'].values():
            provenance['schema:'+schema['sha256']] = m.sha(ROOT/'internal/assets/schemas'/schema['file']) == schema['sha256']
m.result('release-provenance', provenance, {'releases': len(VERSIONS), 'majors': [latest[0]-1,latest[0]], 'kinds': sorted(KINDS)})
identity = json.loads(m.command(m.OUT, 'version', [m.BINARY, 'version', '--json']).stdout)
m.result('machine-identity', {'automaticDefault': identity.get('defaultTargetVersion') == 'auto', 'acceptedReleases': identity.get('acceptedTargetVersions') == VERSIONS,
                            'range': identity.get('supportedVersionRange') == {'minimum':'3.0.0','maximum':'4.1.6'}, 'majors': identity.get('supportedMajors') == [3,4],
                            'automaticSupport': identity.get('automaticVersionSupport') is True}, {'identity':identity})

# All exact releases must perform full schema/local analysis, without old experimental gating.
def resource_values(version, available):
    values=[]
    for kind in available:
        value={'apiVersion':'argoproj.io/v1alpha1','kind':kind,'metadata':{'name':'compatibility'}}
        if kind in ['Workflow','WorkflowTemplate','ClusterWorkflowTemplate']: value['spec']=BASE['spec']
        elif kind=='CronWorkflow': value['spec']=({'schedules':['0 0 * * *']} if version=='auto' or version_tuple(version)>=(4,0,0) else {'schedule':'0 0 * * *'}) | {'workflowSpec':BASE['spec']}
        elif kind=='WorkflowEventBinding': value['spec']={'event':{'selector':'payload.message != ""'}}
        elif kind!='WorkflowTaskResult': value['spec']={}
        values.append(value)
    return values

def release(version):
    available=[src['kind'] for src in next(v for v in LOCK['versions'] if v['version']==version)['sources']]
    values=resource_values(version,available)
    dest,work=m.setup('release-'+version, {'case.yaml':'\n---\n'.join(json.dumps(v) for v in values)+'\n'}, 'schemaVersion: 1\ntargetVersion: "'+version+'"\n')
    code,report = m.cli(dest,work)
    return {'version':version, 'kinds':available, 'pass':code == 0 and report['analysisComplete'] is True and report['notEvaluated'] == [] and report['configurationErrors'] == [] and report['diagnostics'] == [] and report['resourcesAnalyzed'] == len(available) and report['targets'][0]['experimental'] is False}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: release_rows = list(pool.map(release, VERSIONS))
m.save(m.OUT/'releases.json', release_rows)
m.result('every-release-consumer', {'all146':len(release_rows)==146 and '3.0.5' in VERSIONS and '3.2.10' in VERSIONS, 'everyReleaseAccepted':all(x['pass'] for x in release_rows)}, {'failures':[x for x in release_rows if not x['pass']]})

# All kind schemas coexist in one automatic LSP session. The existing 128 MiB
# process budget is retained; Linux exposes its exact high-water mark via procfs.
text='\n---\n'.join(json.dumps(v) for v in resource_values('auto',sorted(KINDS)))+'\n'
dest,work=m.setup('all-kinds-automatic-session',{'case.yaml':text},'');(work/'.argo-workflows-ls.yaml').unlink()
client=m.LSP(dest,work)
try:
    diagnostics=client.open(work/'case.yaml',text);peak=None
    if sys.platform.startswith('linux'):
        peak=int(next(line.split()[1] for line in pathlib.Path('/proc/%d/status'%client.p.pid).read_text().splitlines() if line.startswith('VmHWM:')))
    m.result('all-kinds-automatic-session',{'allEightValid':diagnostics==[],'within128MiB':peak is None or peak<=128*1024}, {'peakRSSKiB':peak,'kindCount':len(KINDS),'memoryMetric':'Linux VmHWM; unavailable on other hosts'})
finally:client.close()

old = copy.deepcopy(BASE); old['spec']['podPriority'] = 1
new = copy.deepcopy(BASE); new['spec']['podResources'] = {'requests':{'cpu':'1'}}
mixed = copy.deepcopy(new); mixed['spec']['podPriority'] = 1
wrong = copy.deepcopy(BASE); wrong['spec']['podResources'] = True
missing = copy.deepcopy(BASE); missing['spec']['resourceClaims'] = [{'resourceClaimName':'claim'}]
check('automatic-old-field',old)
check('automatic-latest-field',new)
check('automatic-incompatible-fields',mixed,error='SCHEMA_UNKNOWN_FIELD')
check('automatic-type-error',wrong,error='SCHEMA_TYPE')
check('automatic-required-field',missing,error='SCHEMA_VALIDATION')
check('exact-old-release',old,'3.0.0')
check('exact-3-6',BASE,'3.6.0')
check('exact-latest',new,'4.1.6')
check('exact-latest-type-error',wrong,'4.1.6',error='SCHEMA_TYPE')
# Keep the historical 3.6 behavior explicit: unsupported fields are warnings.
dest,work,_ = fixture('exact-3-6-new-field',new,'3.6.0');code,report=m.cli(dest,work)
m.result('exact-3-6-new-field', {'strictUnknownField':code==0 and [(d['code'],d['severity']) for d in report['diagnostics']]==[('SCHEMA_UNKNOWN_FIELD','Warning')]}, {'report':report})
for kind in ['WorkflowTemplate','ClusterWorkflowTemplate']:
    value=copy.deepcopy(new);value['kind']=kind;check('automatic-'+kind,value)
cron={'apiVersion':'argoproj.io/v1alpha1','kind':'CronWorkflow','metadata':{'name':'schedule'},'spec':{'schedule':'0 0 * * *','workflowSpec':BASE['spec']}}
check('automatic-old-cron',cron)
cron_new=copy.deepcopy(cron);cron_new['spec'].pop('schedule');cron_new['spec']['schedules']=['0 0 * * *'];cron_new['spec']['workflowSpec']=new['spec'];check('automatic-latest-cron',cron_new)
cron_mixed=copy.deepcopy(cron);cron_mixed['spec']['workflowSpec']=new['spec'];check('automatic-incompatible-cron',cron_mixed,error='SCHEMA_UNKNOWN_FIELD')
event={'apiVersion':'argoproj.io/v1alpha1','kind':'WorkflowEventBinding','metadata':{'name':'incoming'},'spec':{'event':{'selector':'payload.message != ""'},'submit':{'workflowTemplateRef':{'name':'external-template'},'arguments':{'parameters':[{'name':'message','valueFrom':{'event':'payload.message'}}]}}}}
check('automatic-event-binding',event)
event_wrong=copy.deepcopy(event);event_wrong['spec']['event']['selector']=123;check('event-binding-type-error',event_wrong,error='SCHEMA_TYPE')

# Controller CRDs use the same schema path, with their real introduction boundary.
controller_values = {
    'WorkflowArtifactGCTask': {'spec':{'artifactsByNode':{'node':{'artifacts':{}}}}},
    'WorkflowTaskResult': {'outputs':{'parameters':[{'name':'message','value':'{{inputs.parameters.unbound}}'}],'result':'done'}},
    'WorkflowTaskSet': {'spec':{'tasks':{'node':{'name':'worker','http':{'url':'https://example.invalid/{{inputs.parameters.unbound}}'}}}}},
}
for kind,body in controller_values.items():
    value={'apiVersion':'argoproj.io/v1alpha1','kind':kind,'metadata':{'name':'controller-resource'}} | copy.deepcopy(body)
    check('automatic-'+kind,value)
    check('first-release-'+kind,value,INTRODUCED[kind])
    check('latest-release-'+kind,value,VERSIONS[-1])
    prior=[v for v in VERSIONS if version_tuple(v)<version_tuple(INTRODUCED[kind])][-1]
    check('before-introduction-'+kind,value,prior,error='SCHEMA_UNAVAILABLE')
    wrong=copy.deepcopy(value);missing=copy.deepcopy(value)
    if kind=='WorkflowArtifactGCTask': wrong['spec']['artifactsByNode']=True;missing.pop('spec')
    elif kind=='WorkflowTaskResult': wrong['outputs']['result']=True;missing.pop('metadata')
    else: wrong['spec']['tasks']['node']['http']['url']=123;missing['spec']['tasks']['node']['http'].pop('url')
    check('type-error-'+kind,wrong,error='SCHEMA_TYPE')
    check('required-field-'+kind,missing,error='SCHEMA_VALIDATION')

# Even workflow-shaped unknown fields on schema-only kinds must not acquire
# local template scopes, cross-file calls, argument validation or symbol edits.
for kind,body in controller_values.items():
    value={'apiVersion':'argoproj.io/v1alpha1','kind':kind,'metadata':{'name':'schema-only'}} | copy.deepcopy(body)
    value.setdefault('spec',{}).update({'entrypoint':'absent','workflowTemplateRef':{'name':'absent'},'arguments':{'parameters':[{'name':'p'}]},'templates':[{'name':'main','container':{'image':'alpine'}}]})
    dest,work,_=fixture('schema-only-'+kind,value,VERSIONS[-1]);code,report=m.cli(dest,work)
    m.result('schema-only-'+kind,{'schemaWarningsOnly':code==0 and bool(report['diagnostics']) and all(d['code']=='SCHEMA_UNKNOWN_FIELD' and d['severity']=='Warning' for d in report['diagnostics']),
                                'complete':report['analysisComplete'] is True and report['resourcesAnalyzed']==1 and report['notEvaluated']==[]}, {'report':report})

# Exercise all ten upstream CEL expression translations through exact 4.1.6 schema validation.
worker={'name':'worker','container':{'image':'alpine'}}
def dag(): return {'name':'main','dag':{'tasks':[{'name':'a','template':'worker'},{'name':'b','template':'worker'}]}}
cel=[]
v=copy.deepcopy(BASE);v['spec']['arguments']={'artifacts':[{'name':'data','s3':{'bucket':'bucket','key':'key'},'raw':{'data':'value'}}]};cel.append(('artifact-locations',v))
v=copy.deepcopy(BASE);v['spec']['metrics']={'prometheus':[{'name':'duration','help':'Duration','gauge':{'realtime':True,'value':'{{resourcesDuration.cpu}}'}}]};cel.append(('realtime-gauge',v))
for name,fields in [('depends-dependencies',{'depends':'b','dependencies':['b']}),('depends-continue-on',{'depends':'b','continueOn':{'failed':True}}),('numeric-dependent-name',{'name':'1','depends':'b'}),('sequence-count-end',{'withSequence':{'count':'1','end':'2'}})]:
    v=copy.deepcopy(BASE);v['spec']['templates']=[dag(),worker];v['spec']['templates'][0]['dag']['tasks'][0].update(fields);cel.append((name,v))
v=copy.deepcopy(BASE);v['spec']['templates']=[{'name':'main','resource':{'action':'create','manifest':'apiVersion: v1\nkind: ConfigMap','manifestFrom':{'artifact':{'name':'manifest'}}}}];cel.append(('manifest-sources',v))
v=copy.deepcopy(BASE);v['spec']['templates'][0]['suspend']={};cel.append(('template-types',v))
for name,fields in [('dag-timeout',{'timeout':'1m'}),('dag-deadline',{'activeDeadlineSeconds':1})]:
    v=copy.deepcopy(BASE);v['spec']['templates']=[dag(),worker];v['spec']['templates'][0].update(fields);cel.append((name,v))
for name,value in cel:check('cel-'+name,value,'4.1.6',error='SCHEMA_VALIDATION')

# Completion and hover select compatible whole-release branches, using real LSP requests.
head='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: queries}\nspec:\n'
body='  entrypoint: main\n  templates:\n    - name: main\n      container: {image: alpine}\n'
queries=[('new-completion',head+body+'  podR§\n','completion','podResources',True),
         ('old-completion',head+body+'  podPr§\n','completion','podPriority',True),
         ('incompatible-completion',head+'  podPriority: 1\n'+body+'  podR§\n','completion','podResources',False),
         ('old-hover',head+'  podPr§iority: 1\n'+body,'hover','integer',True),
         ('new-hover',head+'  podRes§ources: {requests: {cpu: "1"}}\n'+body,'hover','object',True)]
for kind in ['WorkflowTemplate','ClusterWorkflowTemplate']:
    typed_head=head.replace('kind: Workflow\n','kind: '+kind+'\n')
    queries.extend([(kind+'-completion',typed_head+body+'  paralleli§\n','completion','parallelism',True),
                    (kind+'-hover',typed_head+body+'  paralleli§sm: 2\n','hover','integer',True)])
other_queries = {
    'CronWorkflow': ('spec:\n  schedu§\n', 'spec:\n  schedu§les: ["0 0 * * *"]\n', 'schedules', 'array'),
    'WorkflowEventBinding': ('spec:\n  event:\n    sele§\n', 'spec:\n  event:\n    sele§ctor: "payload != null"\n', 'selector', 'string'),
    'WorkflowArtifactGCTask': ('spec:\n  artifactsB§\n', 'spec:\n  artifactsB§yNode: {}\n', 'artifactsByNode', 'object'),
    'WorkflowTaskResult': ('outp§\n', 'outp§uts: {result: done}\n', 'outputs', 'object'),
    'WorkflowTaskSet': ('spec:\n  ta§\n', 'spec:\n  ta§sks: {}\n', 'tasks', 'object'),
}
for kind,(complete,hover,label,typ) in other_queries.items():
    typed_head='apiVersion: argoproj.io/v1alpha1\nkind: '+kind+'\nmetadata: {name: query}\n'
    queries.extend([(kind+'-completion',typed_head+complete,'completion',label,True),(kind+'-hover',typed_head+hover,'hover',typ,True)])

def query(name,marked,method,needle,present,version=None,minimum=None):
    text=marked.replace('§','')
    dest,work=m.setup(name,{'case.yaml':text},'' if version is None else 'schemaVersion: 1\ntargetVersion: "'+version+'"\n')
    if version is None:(work/'.argo-workflows-ls.yaml').unlink()
    path=work/'case.yaml';client=m.LSP(dest,work)
    try:
        client.open(path,text);response=client.request('textDocument/'+method,{'textDocument':{'uri':path.as_uri()},'position':m.pos(marked,'§')})
        result=response.get('result');content=(result or {}).get('contents',{}).get('value','') if method=='hover' else ''
        matched=any(x['label']==needle for x in (result or [])) if method=='completion' else 'Type: `'+needle+'`' in content
        checks={'noRequestError':'error' not in response,'consistent':matched is present,'zeroConfig':version is not None or not (work/'.argo-workflows-ls.yaml').exists()}
        if minimum:checks['sourceAvailability']='supported releases '+minimum+'–4.1.6' in content
        if version and not present:checks['noUnavailableSchemaFallback']=not result
        m.result(name,checks,{'response':response})
    finally:client.close()

for name,marked,method,needle,present in queries:
    kind=name.removesuffix('-hover')
    query(name,marked,method,needle,present,minimum=INTRODUCED.get(kind) if method=='hover' else None)
for kind in INTRODUCED:
    complete,hover,label,typ=other_queries[kind]
    typed_head='apiVersion: argoproj.io/v1alpha1\nkind: '+kind+'\nmetadata: {name: unavailable}\n'
    query('unavailable-'+kind+'-completion',typed_head+complete,'completion',label,False,version='3.0.0')
    query('unavailable-'+kind+'-hover',typed_head+hover,'hover',typ,False,version='3.0.0')

# Python's children usage is observational; the enforced regression budgets live in bounded suites.
try:
    import resource
    peak=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
except ImportError: peak=None
m.save(m.OUT/'manifest.json', {'command':sys.argv,'binarySha256':m.sha(m.BINARY),'testSha256':m.sha(pathlib.Path(__file__)),'catalogSha256':m.sha(ROOT/'compat/catalog.lock.json'),'releaseCount':len(VERSIONS),'resourceVersionPairs':sum(len(r['kinds']) for r in release_rows),'kinds':sorted(KINDS),'celRulesExercised':len(cel),'peakChildRSSPlatformUnits':peak,'scope':'CRD schema and local editing, not controller execution or cluster admission'})
raise SystemExit(0 if all(x['pass'] for x in m.RESULTS) else 1)
