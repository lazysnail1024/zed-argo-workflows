#!/usr/bin/env python3
"""Five independently checked full-workspace samples under real cgroup limits."""
import pathlib,sys,subprocess,time,os,json,hashlib
binary=pathlib.Path(sys.argv[1]).resolve();work=pathlib.Path(sys.argv[2]).resolve();out=pathlib.Path(sys.argv[3]).resolve();out.mkdir(parents=True,exist_ok=True)
relative=pathlib.Path('/proc/self/cgroup').read_text().strip().split('::',1)[1];cg=pathlib.Path('/sys/fs/cgroup')/relative.lstrip('/');constraints={n:(cg/n).read_text().strip() for n in ['cpu.max','memory.max']};constraints['affinity']=sorted(os.sched_getaffinity(0));quota,period=constraints['cpu.max'].split();assert quota!='max' and int(quota)/int(period)<=4 and len(constraints['affinity'])==4;assert constraints['memory.max']!='max' and int(constraints['memory.max'])<=8*1024**3
manifest=json.loads((work/'workload-manifest.json').read_text());assert manifest['yamlFiles']==10000 and manifest['yamlBytes']==50*1024**2 and manifest['declarations']+manifest['references']==30000
samples=int(sys.argv[4]) if len(sys.argv)>4 else 5;assert samples>0
rows=[]
for sample in range(samples):
 cmd=[str(binary),'check','--root',str(work),'--format','json'];start=time.monotonic();peak=0
 with (out/('%d.stdout'%sample)).open('wb') as so,(out/('%d.stderr'%sample)).open('wb') as se:
  p=subprocess.Popen(cmd,stdout=so,stderr=se)
  while p.poll() is None:
   try:peak=max(peak,int(next(x.split()[1] for x in pathlib.Path('/proc/%d/status'%p.pid).read_text().splitlines() if x.startswith('VmRSS:'))))
   except (OSError,StopIteration):pass
   if time.monotonic()-start>8:p.kill();p.wait();break
   time.sleep(.01)
 elapsed=time.monotonic()-start
 try:r=json.loads((out/('%d.stdout'%sample)).read_text())
 except ValueError:r={}
 # Both pinned releases are supported and independently retain their strict schemas.
 expected_targets={('group-%03d'%i,'3.5.0' if i%2 else '3.6.0',True,False) for i in range(100)}
 targets=r.get('targets',[])
 actual_targets={(t.get('scope'),t.get('version'),t.get('analysisComplete'),t.get('experimental')) for t in targets}
 checks={'allFiles':r.get('filesAnalyzed')==10000,'allResources':r.get('resourcesAnalyzed')==10000,'withinFiveSeconds':elapsed<=5,'mixedTargetExit':p.returncode==0,'honestCompleteness':r.get('analysisComplete') is True,'allTargetsEvaluated':r.get('notEvaluated')==[],'exactScopeTargets':len(targets)==100 and actual_targets==expected_targets,'noDiagnostics':r.get('diagnostics')==[],'noConfigurationError':r.get('configurationErrors')==[],'reportIdentity':r.get('product')=='argo-workflows' and r.get('reportSchemaVersion')==1 and r.get('validationScope')=='local-editing-contract'}
 rows.append({'sample':sample,'command':cmd,'exitCode':p.returncode,'seconds':elapsed,'peakRSSKiB':peak,'assertions':checks,'pass':all(checks.values())});(out/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'manifest.json').write_text(json.dumps({'samples':samples,'constraints':constraints,'cgroup':relative,'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'workloadManifestSha256':sha(work/'workload-manifest.json'),'python':sys.version},indent=2)+'\n');sys.exit(0 if len(rows)==samples and all(r['pass'] for r in rows) else 1)
