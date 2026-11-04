#!/usr/bin/env python3
"""Generate exact byte/file/symbol workloads with independent semantic totals."""
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]).resolve();root.mkdir(parents=True,exist_ok=True)
count=10000;total=50*1024*1024;base,extra=divmod(total,count);hashes={}
config=['schemaVersion: 1','targetVersion: "3.6.0"','referencePolicy: closed','scopes:']
for scope in range(100):
 config+=['  - name: group-%03d'%scope,'    include: ["group-%03d/*.yaml"]'%scope,'    targetVersion: "%s"'%('3.5.0' if scope%2 else '3.6.0')]
(root/'.argo-workflows-ls.yaml').write_text('\n'.join(config)+'\n')
for i in range(count):
 text=('apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: workload-%05d}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      container: {image: alpine}\n    - name: unused\n      container: {image: alpine}\n'%i).encode()
 size=base+(i<extra);text+=b'#'+b'x'*(size-len(text)-2)+b'\n';assert len(text)==size
 path=root/('group-%03d'% (i//100))/('%05d.yaml'%i);path.parent.mkdir(exist_ok=True);path.write_bytes(text);hashes[str(path.relative_to(root))]=hashlib.sha256(text).hexdigest()
report={'yamlFiles':count,'yamlBytes':total,'ownedResources':count,'declarations':2*count,'references':count,'semanticCountingRule':'Per file: two local template declarations and one entrypoint reference. Metadata names are not counted.','scopeCount':100,'filesPerScope':100,'versions':['3.5.0','3.6.0'],'configurationBytesExcludedFromYamlTotal':(root/'.argo-workflows-ls.yaml').stat().st_size,'files':hashes}
(root/'workload-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
