#!/usr/bin/env python3
"""Exercise uncancellable Expr parsing through the real CLI under a hard memory bound."""
import pathlib,sys,json,subprocess,time,os,signal,hashlib
binary=pathlib.Path(sys.argv[1]).resolve();out=pathlib.Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
cgpath=pathlib.Path('/proc/self/cgroup').read_text().strip().split('::',1)[1];cg=pathlib.Path('/sys/fs/cgroup')/cgpath.lstrip('/');limit=(cg/'memory.max').read_text().strip();assert limit!='max' and int(limit)<=512*1024**2
cases=[('parentheses-256','('*256+'inputs.parameters.alpha'+')'*256,True),('parentheses-257','('*257+'inputs.parameters.alpha'+')'*257,False),('deep-parentheses','('*30000+'inputs.parameters.alpha'+')'*30000,False),('long-unary','!'*30000+'inputs.parameters.alpha',False),('right-associative','1 ** '*30000+'inputs.parameters.alpha',False),('left-associative','1 + '*30000+'inputs.parameters.alpha',False),('many-lets',''.join('let v%d = 1; '%i for i in range(10000))+'inputs.parameters.alpha',False),('large-literal',repr('x'*900000)+' + inputs.parameters.alpha',True),('large-malformed','1 + '*30000,False),('large-literal-malformed',repr('x'*900000)+' +',False)]
cases += [('near-limit-left-depth','1+'*400000+'inputs.parameters.alpha',False),('near-limit-wide-array','string(['+'1,'*399999+'1]) + inputs.parameters.alpha',True)]
selected=set(filter(None,os.environ.get('WF_EXPR_CASES','').split(',')));rows=[]
if selected-set(name for name,_,_ in cases):raise SystemExit('unknown expression case selection')
for name,expr,positive in cases:
 if selected and name not in selected:continue
 folder=out/name;folder.mkdir(exist_ok=True);work=folder/'input';work.mkdir(exist_ok=True);(work/'.argo-workflows-ls.yaml').write_text('schemaVersion: 1\ntargetVersion: "3.6.0"\nreferencePolicy: closed\n')
 text='apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: bounded}\nspec:\n  entrypoint: main\n  templates:\n    - name: main\n      inputs:\n        parameters: [{name: alpha, default: supplied}]\n      container:\n        image: alpine\n        args: ['+json.dumps('{{='+expr+'}}')+']\n';(work/'case.yaml').write_text(text)
 command=[str(binary),'check','--root',str(work),'--format','json'];start=time.monotonic();peak=0;killed=False
 with (folder/'stdout.raw').open('wb') as so,(folder/'stderr.raw').open('wb') as se:
  p=subprocess.Popen(command,stdout=so,stderr=se,start_new_session=True)
  while p.poll() is None:
   peak=max(peak,int((cg/'memory.current').read_text()))
   if peak>384*1024**2 or time.monotonic()-start>7:killed=True;os.killpg(p.pid,signal.SIGKILL);break
   time.sleep(.005)
  p.wait()
 try:report=json.loads((folder/'stdout.raw').read_text())
 except ValueError:report={}
 elapsed=time.monotonic()-start
 reasons=report.get('notEvaluated',[]);diagnostics=report.get('diagnostics',[])
 malformed_reason={'large-malformed':'invalid expression: unexpected token EOF (1:120000)',
                   'large-literal-malformed':'invalid expression: unexpected token EOF (1:900004)'}
 expected_reason=malformed_reason.get(name,'invalid expression: expression syntax depth exceeds 256')
 reason_matches=len(reasons)==1 and isinstance(reasons[0],str) and (reasons[0].partition('\n')[0]==expected_reason if name in malformed_reason else reasons[0]==expected_reason)
 checks={'terminalJSON':bool(report),'bounded':not killed and elapsed<5.25,
         'expectedExit':p.returncode==(0 if positive else 2),
         'reportIdentity':report.get('product')=='argo-workflows' and report.get('reportSchemaVersion')==1 and report.get('validationScope')=='local-editing-contract',
         'noConfigurationError':report.get('configurationErrors')==[],
         'completeness':report.get('analysisComplete') is positive,
         'positiveCompleted':not positive or (report.get('filesAnalyzed')==1 and report.get('resourcesAnalyzed')==1 and diagnostics==[] and reasons==[]),
         'negativeExplained':positive or reason_matches,
         'negativeDiagnostic':positive or (len(reasons)==1 and len(diagnostics)==1 and diagnostics[0].get('code')=='WF_REFERENCE_INCOMPLETE' and diagnostics[0].get('severity')=='Warning' and diagnostics[0].get('uri')==(work/'case.yaml').as_uri() and diagnostics[0].get('message')=='Reference analysis incomplete: '+reasons[0])}
 rows.append({'case':name,'positive':positive,'command':command,'exitCode':p.returncode,'seconds':elapsed,'peakCgroupBytes':peak,'harnessKilled':killed,'assertions':checks,'pass':all(checks.values())});(out/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();(out/'manifest.json').write_text(json.dumps({'binarySha256':sha(binary),'testSha256':sha(pathlib.Path(__file__)),'cgroup':cgpath,'memory.max':limit},indent=2)+'\n');sys.exit(0 if len(rows)==(len(selected) if selected else len(cases)) and all(r['pass'] for r in rows) else 1)
