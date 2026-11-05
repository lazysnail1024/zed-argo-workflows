#!/usr/bin/env python3
"""Normalize only locked CRD bytes; reject unreviewed constraint keywords."""
import hashlib,itertools,json,pathlib,re,yaml
root=pathlib.Path(__file__).resolve().parents[1];catalog_path=root/'compat/catalog.lock.json';catalog=json.loads(catalog_path.read_text());out=root/'internal/assets/schemas';out.mkdir(parents=True,exist_ok=True)
allowed={'type','properties','required','items','format','default','anyOf','allOf','oneOf','not','additionalProperties','pattern','enum','const','description','title','nullable','minimum','maximum','exclusiveMinimum','exclusiveMaximum','minLength','maxLength','minItems','maxItems','uniqueItems','minProperties','maxProperties','multipleOf','x-kubernetes-map-type','x-kubernetes-int-or-string','x-kubernetes-list-type','x-kubernetes-list-map-keys','x-kubernetes-preserve-unknown-fields','x-kubernetes-validations'}
# These are exact translations of the ten CEL rules in the locked 4.x CRDs.
# Do not ignore future rules or run a general expression interpreter at runtime.
cel_rules=set()
def cel_schema(rule):
 cel_rules.add(rule)
 artifact=['s3','git','http','artifactory','hdfs','raw','oss','gcs','azure','plugin']
 templates=['container','script','dag','steps','resource','suspend','containerSet','data','http','plugin']
 def count_expression(names,base):return ' + '.join('(has('+base+'.'+n+') ? 1 : 0)' for n in names)+' <= 1'
 def at_most_one(names):return {'allOf':[{'not':{'required':[a,b]}} for a,b in itertools.combinations(names,2)]}
 if rule==count_expression(artifact,'self'):return at_most_one(artifact)
 if rule=='self.all(t, '+count_expression(templates,'t')+')':return {'items':at_most_one(templates)}
 pairs={
  '!has(self.depends) || !has(self.dependencies)':['depends','dependencies'],
  '!has(self.depends) || !has(self.continueOn)':['depends','continueOn'],
  '!(has(self.count) && has(self.end))':['count','end'],
  '(has(self.manifest) && !has(self.manifestFrom)) || (!has(self.manifest) && has(self.manifestFrom)) || (!has(self.manifest) && !has(self.manifestFrom))':['manifest','manifestFrom'],
 }
 if rule in pairs:return {'not':{'required':pairs[rule]}}
 if rule=="!has(self.realtime) || !self.realtime || !self.value.contains('resourcesDuration.')":
  return {'not':{'required':['realtime','value'],'properties':{'realtime':{'const':True},'value':{'pattern':r'resourcesDuration\.'}}}}
 if rule=="!(has(self.depends) || has(self.dependencies)) || !self.name.matches('^[0-9]')":
  return {'not':{'required':['name'],'properties':{'name':{'pattern':'^[0-9]'}},'anyOf':[{'required':['depends']},{'required':['dependencies']}]}}
 if rule=='self.all(t, !(has(t.timeout) && t.timeout != "" && (has(t.steps) || has(t.dag))))':
  return {'items':{'not':{'required':['timeout'],'properties':{'timeout':{'not':{'const':''}}},'anyOf':[{'required':['steps']},{'required':['dag']}]}}}
 if rule=='self.all(t, !(has(t.activeDeadlineSeconds) && (has(t.steps) || has(t.dag))))':
  return {'items':{'not':{'required':['activeDeadlineSeconds'],'anyOf':[{'required':['steps']},{'required':['dag']}]}}}
 raise ValueError('unreviewed CEL rule: '+rule)

def normalize(value):
 if isinstance(value,bool):return value
 if not isinstance(value,dict):raise ValueError('schema must be an object or boolean')
 unknown=set(value)-allowed
 if unknown:raise ValueError('unreviewed Schema keywords: '+str(unknown))
 result=dict(value)
 validations=result.pop('x-kubernetes-validations',[])
 if any(set(v)-{'rule','message'} for v in validations):raise ValueError('unreviewed CEL validation options')
 if result.pop('nullable',False):
  typ=result.get('type')
  if isinstance(typ,str):result['type']=[typ,'null']
  elif typ:result['type']=list(set(typ+['null']))
 for key,bound in [('exclusiveMinimum','minimum'),('exclusiveMaximum','maximum')]:
  if isinstance(result.get(key),bool):
   exclusive=result.pop(key)
   if exclusive:
    if bound not in result:raise ValueError('exclusive bound missing threshold')
    result[key]=result.pop(bound)
 if result.get('x-kubernetes-int-or-string') and 'anyOf' not in result:result['anyOf']=[{'type':'integer'},{'type':'string'}]
 for key in ['properties']:
  if key in result:result[key]={k:normalize(v) for k,v in result[key].items()}
 for key in ['items','additionalProperties','not']:
  if key in result:result[key]=normalize(result[key])
 for key in ['allOf','anyOf','oneOf']:
  if key in result:result[key]=[normalize(v) for v in result[key]]
 if validations:result.setdefault('allOf',[]).extend(cel_schema(v['rule']) for v in validations)
 return result
cache={}
assert catalog['collectionComplete'] and not catalog['errors'], 'upstream collection must be complete'
expected_kinds=set(catalog['supportedKinds'])
for version in catalog['versions']:
 present=[s['kind'] for s in version['sources']];absent=[s['kind'] for s in version.get('absentSources',[])]
 assert len(present)==len(set(present)) and len(absent)==len(set(absent)), 'duplicate resource kind'
 assert not set(present)&set(absent) and set(present)|set(absent)==expected_kinds, 'incomplete resource-kind inventory'
 for source in version['sources']:
  digest=source['sha256']
  if digest not in cache:
   raw=(root/source['path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==digest
   crd=yaml.load(raw,Loader=yaml.CSafeLoader);assert crd['spec']['group']=='argoproj.io' and crd['spec']['names']['kind']==source['kind']
   schemas={}
   for v in crd['spec']['versions']:
    if 'schema' not in v:raise ValueError('missing version schema')
    schema=normalize(v['schema']['openAPIV3Schema']);schema['$schema']='https://json-schema.org/draft/2020-12/schema';data=json.dumps(schema,sort_keys=True,separators=(',',':')).encode();sha=hashlib.sha256(data).hexdigest();(out/(sha+'.json')).write_bytes(data);schemas['argoproj.io/'+v['name']]={'sha256':sha,'file':sha+'.json'}
   cache[digest]=schemas
  source['normalizedSchemas']=cache[digest]
 version['schemaSha256']=hashlib.sha256(json.dumps([s['normalizedSchemas'] for s in version['sources']],sort_keys=True,separators=(',',':')).encode()).hexdigest()
# Keep each release as a whole branch. Closing structured objects here prevents
# a pre-feature schema from accepting a new field with an invalid type. Exact
# release schemas retain their upstream additionalProperties policy.
def automatic_schema(schemas):
 definitions={}
 def intern(node):
  if isinstance(node,bool):return node
  value=dict(node)
  value.pop('$schema',None)
  if value.get('type')=='object' and value.get('properties') and 'additionalProperties' not in value and not value.get('x-kubernetes-preserve-unknown-fields',False):
   value['additionalProperties']=False
  if 'properties' in value:value['properties']={k:intern(v) for k,v in value['properties'].items()}
  for key in ['items','additionalProperties','not']:
   if isinstance(value.get(key),(dict,bool)):value[key]=intern(value[key])
  for key in ['allOf','anyOf','oneOf']:
   if key in value:value[key]=[intern(v) for v in value[key]]
  payload=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
  # Only factor nontrivial nodes. Tiny scalars are cheaper inline than a ref.
  if len(payload)<160:return value
  digest=hashlib.sha256(payload).hexdigest()
  definitions[digest]=value
  return {'$ref':'#/$defs/'+digest}
 branches=[]
 for schema in schemas:
  # Root prose only restates the kind already known to the editor. Omitting it
  # from automatic alternatives lets identical namespaced/cluster template
  # contracts share one embedded file and compiled graph. Field docs stay intact.
  schema=dict(schema);schema.pop('description',None)
  branch=intern(schema)
  if branch not in branches:branches.append(branch)
 return {'$schema':'https://json-schema.org/draft/2020-12/schema','anyOf':branches,'$defs':definitions}

automatic_sources=[]
for kind in sorted({s['kind'] for v in catalog['versions'] for s in v['sources']}):
 source_schemas={}
 for version in catalog['versions']:
  for source in version['sources']:
   if source['kind']==kind:
    for api,schema in source['normalizedSchemas'].items():source_schemas.setdefault(api,{})[schema['file']]=None
 normalized={}
 for api,files in source_schemas.items():
  schema=automatic_schema([json.loads((out/f).read_text()) for f in reversed(files)])
  data=json.dumps(schema,sort_keys=True,separators=(',',':')).encode();sha=hashlib.sha256(data).hexdigest();(out/(sha+'.json')).write_bytes(data)
  normalized[api]={'sha256':sha,'file':sha+'.json','branchCount':len(schema['anyOf'])}
 automatic_sources.append({'kind':kind,'url':'https://github.com/argoproj/argo-workflows/releases','availableVersions':[v['version'] for v in catalog['versions'] if any(s['kind']==kind for s in v['sources'])],'normalizedSchemas':normalized})
catalog['automatic']={'version':'auto','sources':automatic_sources,'schemaSha256':hashlib.sha256(json.dumps(automatic_sources,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
catalog['defaultVersion']='auto'
catalog['analysisReady']=True
catalog['supportContract']='Embedded CRD validation and local editing; controller execution and admission behavior are not simulated.'
catalog_path.write_text(json.dumps(catalog,indent=2)+'\n')
# Retain only catalog-referenced schemas: embed globs must not keep obsolete
# generated unions or pre-translation copies after an upstream refresh.
referenced={s['file'] for v in catalog['versions']+[catalog['automatic']] for src in v['sources'] for s in src['normalizedSchemas'].values()}
for path in out.glob('*.json'):
 if path.name not in referenced:path.unlink()
report={'command':'python3 scripts/normalize-schemas.py','pyyamlVersion':yaml.__version__,'sources':len(cache),'schemas':len(list(out.glob('*.json'))),'catalogSha256':hashlib.sha256(catalog_path.read_bytes()).hexdigest(),'schemaAcceptance':'locked upstream conversion; complete schema alternatives for automatic editing','unknownKeywords':'rejected','celConstraints':len(cel_rules),'automaticSources':automatic_sources}
(root/'artifacts/wf2-normalization.json').write_text(json.dumps(report,indent=2)+'\n')
