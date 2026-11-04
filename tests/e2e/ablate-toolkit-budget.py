#!/usr/bin/env python3
"""Compare the previous dependency delivery's real binary with the new budget E2E."""
import pathlib,sys,subprocess,tempfile,tarfile,os,hashlib,json,resource,shutil
root=pathlib.Path(__file__).resolve().parents[2];archive=pathlib.Path(sys.argv[1]).resolve();out=root/'artifacts/wf-toolkit-upgrade/old-dependency';out.mkdir(parents=True,exist_ok=True)
env=os.environ|{'GOROOT':'/tmp/zed-argo-go-mod/golang.org/toolchain@v0.0.1-go1.27.3.linux-amd64','GOMODCACHE':'/tmp/zed-argo-go-mod','GOCACHE':'/tmp/zed-argo-go-build','GOWORK':'off','GOPROXY':'off'}
with tempfile.TemporaryDirectory(prefix='wf-old-toolkit-') as d:
 with tarfile.open(archive) as tar:tar.extractall(d,filter='data')
 cmd=[env['GOROOT']+'/bin/go','build','-mod=vendor','-buildvcs=false','-trimpath','-o',str(out/'server'),'./cmd/argo-workflows-ls'];build=subprocess.run(cmd,cwd=d,env=env,capture_output=True);(out/'build.stdout').write_bytes(build.stdout);(out/'build.stderr').write_bytes(build.stderr)
 report={'buildCommand':cmd,'buildExitCode':build.returncode,'archiveSha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'archive':str(archive),'detected':False,'comparison':'previous frozen source delivery, before dependency upgrade'}
 if build.returncode==0:
  resource.setrlimit(resource.RLIMIT_CORE,(0,0))
  cmd=[sys.executable,str(root/'tests/e2e/wf-toolkit-budget.py'),str(out/'server'),str(out/'run')];p=subprocess.run(cmd,capture_output=True,timeout=40);(out/'runner.stdout').write_bytes(p.stdout);(out/'runner.stderr').write_bytes(p.stderr)
  actual=json.loads((out/'run/depth-budget/check.command.json').read_text());report.update(command=cmd,exitCode=p.returncode,binarySha256=hashlib.sha256((out/'server').read_bytes()).hexdigest(),cliExitCode=actual['exitCode'],detected=p.returncode==1 and (actual['exitCode']<0 or b'SIGABRT' in (out/'run/depth-budget/check.stderr').read_bytes()),decision='retain budget process consumer; prior dependency aborts instead of returning incomplete')
 report['driverSha256']=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest();(out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));sys.exit(0 if report['detected'] else 1)
