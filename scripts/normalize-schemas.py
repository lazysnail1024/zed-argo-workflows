#!/usr/bin/env python3
"""Normalize only locked CRD bytes; reject unreviewed constraint keywords."""
import hashlib,json,pathlib,yaml
root=pathlib.Path(__file__).resolve().parents[1];catalog_path=root/'compat/catalog.lock.json';catalog=json.loads(catalog_path.read_text());out=root/'internal/assets/schemas';out.mkdir(parents=True,exist_ok=True)
allowed={'type','properties','required','items','format','default','anyOf','allOf','oneOf','not','additionalProperties','pattern','enum','const','description','title','nullable','minimum','maximum','exclusiveMinimum','exclusiveMaximum','minLength','maxLength','minItems','maxItems','uniqueItems','minProperties','maxProperties','multipleOf','x-kubernetes-map-type','x-kubernetes-int-or-string','x-kubernetes-list-type','x-kubernetes-list-map-keys','x-kubernetes-preserve-unknown-fields'}
def normalize(value):
 if isinstance(value,bool):return value
 if not isinstance(value,dict):raise ValueError('schema must be an object or boolean')
 unknown=set(value)-allowed
 if unknown:raise ValueError('unreviewed Schema keywords: '+str(unknown))
 result=dict(value)
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
 return result
cache={}
for version in catalog['versions']:
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
catalog_path.write_text(json.dumps(catalog,indent=2)+'\n')
report={'command':'python3 scripts/normalize-schemas.py','pyyamlVersion':yaml.__version__,'sources':len(cache),'schemas':len(list(out.glob('*.json'))),'catalogSha256':hashlib.sha256(catalog_path.read_bytes()).hexdigest(),'schemaAcceptance':'not complete; source conversion only','unknownKeywords':'rejected','celConstraints':0}
(root/'artifacts/wf2-normalization.json').write_text(json.dumps(report,indent=2)+'\n')
