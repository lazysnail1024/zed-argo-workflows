"""Specific retained-evidence counterexamples for the lifecycle consumer."""
import copy
import json

FAULTS=('invalid-phase-json','empty-phase-list','missing-timeline','wrong-sent-buffer','missing-document-events','truncated-phase','wrong-phase-expectation','bad-current-publication')

def corrupt(folder,fault):
 phases_path=folder/'diagnostic-phases.json';traffic_path=folder/'traffic.json'
 if fault=='invalid-phase-json':phases_path.write_text('{ invalid JSON');return
 phases=json.loads(phases_path.read_text());traffic=json.loads(traffic_path.read_text())
 if fault=='empty-phase-list':phases['phases']=[]
 elif fault=='missing-timeline':traffic.pop('timeline')
 elif fault=='wrong-sent-buffer':
  for message in traffic['sent']+[e['message'] for e in traffic['timeline'] if e['direction']=='sent']:
   if message.get('method')=='textDocument/didOpen' and message['params']['textDocument']['uri'].endswith('/workflow.yaml'):message['params']['textDocument']['text']='apiVersion: v1\nkind: ConfigMap\n'
 elif fault=='missing-document-events':
  traffic['sent']=[m for m in traffic['sent'] if not m.get('method','').startswith('textDocument/')]
  traffic['timeline']=[e for e in traffic['timeline'] if not (e['direction']=='sent' and e['message'].get('method','').startswith('textDocument/'))]
 elif fault=='truncated-phase':phases['phases'][1]['end']=phases['phases'][1]['start']+1
 elif fault=='wrong-phase-expectation':phases['phases'][1]['diagnostics']=phases['phases'][0]['diagnostics']
 elif fault=='bad-current-publication':
  index=phases['phases'][1]['start']+1;message=copy.deepcopy(traffic['timeline'][index-1]['message']);message['params']['diagnostics']=phases['phases'][0]['diagnostics']
  received_index=sum(e['direction']=='received' for e in traffic['timeline'][:index]);traffic['received'].insert(received_index,message);traffic['timeline'].insert(index,{'direction':'received','message':message})
  for p in phases['phases']:
   for key in ('start','end'):
    if p[key] is not None and p[key]>=index:p[key]+=1
  raw=bytearray()
  for m in traffic['received']:
   data=json.dumps(m).encode();raw.extend(f'Content-Length: {len(data)}\r\n\r\n'.encode()+data)
  (folder/'stdout.raw').write_bytes(raw)
 else:raise ValueError(fault)
 phases_path.write_text(json.dumps(phases));traffic_path.write_text(json.dumps(traffic))
