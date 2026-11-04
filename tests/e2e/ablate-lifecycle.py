#!/usr/bin/env python3
import pathlib,sys,tempfile,shutil,subprocess,os,json,hashlib
root=pathlib.Path(__file__).resolve().parents[2];out=root/'artifacts/wf-lifecycle-ablation';out.mkdir(parents=True,exist_ok=True)
variants=[('nested-root','internal/workspace/disk.go','if path == child && path != absolute {','if false && path == child && path != absolute {','nested-root-single-owner'),('cancel','internal/lsp/serve.go','if m.Method == "$/cancelRequest" {','if m.Method == "$/cancelRequest" { continue }; if m.Method == "$/cancelRequest" {','cancel-acknowledged')]
env=os.environ|{'GOROOT':'/tmp/zed-argo-go-mod/golang.org/toolchain@v0.0.1-go1.27.3.linux-amd64','GOMODCACHE':'/tmp/zed-argo-go-mod','GOCACHE':'/tmp/zed-argo-go-build','GOWORK':'off','GOPROXY':'off'};reports=[]
for name,file,before,after,expected in variants:
 dest=out/name;dest.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='wf-lifecycle-ablation-') as d:
  copy=pathlib.Path(d)
  for directory in ['cmd','compat','internal','vendor']:shutil.copytree(root/directory,copy/directory)
  for filename in ['go.mod','go.sum']:shutil.copy2(root/filename,copy/filename)
  p=copy/file;text=p.read_text();assert before in text;p.write_text(text.replace(before,after));shutil.copy2(p,dest/'mutant.go')
  command=[env['GOROOT']+'/bin/go','build','-mod=vendor','-buildvcs=false','-trimpath','-o',str(dest/'server'),'./cmd/argo-workflows-ls'];build=subprocess.run(command,cwd=copy,env=env,capture_output=True);(dest/'build.stdout').write_bytes(build.stdout);(dest/'build.stderr').write_bytes(build.stderr);report={'name':name,'buildCommand':command,'buildExitCode':build.returncode,'detected':False}
  if build.returncode==0:
   command=[sys.executable,str(root/'tests/e2e/wf-lifecycle.py'),str(dest/'server'),str(dest/'run')];p=subprocess.run(command,capture_output=True,timeout=150);(dest/'runner.stdout').write_bytes(p.stdout);(dest/'runner.stderr').write_bytes(p.stderr);results=json.loads((dest/'run/results.json').read_text());report.update(command=command,exitCode=p.returncode,binarySha256=hashlib.sha256((dest/'server').read_bytes()).hexdigest(),detected=p.returncode==1 and any(x['case']==expected and not x['pass'] for x in results),decision='retain guard when independently detected')
  reports.append(report);(out/'report.json').write_text(json.dumps({'pythonVersion':sys.version,'driverSha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'variants':reports},indent=2));print(name,report['detected'],flush=True)
sys.exit(0 if len(reports)==2 and all(x['detected'] for x in reports) else 1)
