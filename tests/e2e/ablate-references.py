#!/usr/bin/env python3
import pathlib,tempfile,shutil,subprocess,os,json,hashlib
root=pathlib.Path(__file__).resolve().parents[2];out=root/'artifacts/wf4-references-ablation';out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='wf4-references-ablation-') as d:
 copy=pathlib.Path(d)
 for directory in ['cmd','compat','internal']:shutil.copytree(root/directory,copy/directory)
 for name in ['go.mod','go.sum']:shutil.copy(root/name,copy/name)
 if (root/'vendor').exists():shutil.copytree(root/'vendor',copy/'vendor')
 p=copy/'internal/analysis/references.go';s=p.read_text();before='if !ok {\n\t\t\t\t\t\t\temit(node, "WF_REFERENCE_SCOPE"';assert before in s;p.write_text(s.replace(before,'if !ok && false {\n\t\t\t\t\t\t\temit(node, "WF_REFERENCE_SCOPE"'))
 env=os.environ|{'GOROOT':'/tmp/zed-argo-go-mod/golang.org/toolchain@v0.0.1-go1.27.3.linux-amd64','GOMODCACHE':'/tmp/zed-argo-go-mod','GOCACHE':'/tmp/zed-argo-go-build','GOWORK':'off'};cmd=[env['GOROOT']+'/bin/go','build','-buildvcs=false','-trimpath','-o',str(copy/'server'),'./cmd/argo-workflows-ls'];p=subprocess.run(cmd,cwd=copy,env=env,capture_output=True);(out/'build.stdout').write_bytes(p.stdout);(out/'build.stderr').write_bytes(p.stderr)
 assert p.returncode==0,'compile failure cannot detect behavior';shutil.copy(copy/'server',out/'mutant-server');cmd=['python3',str(root/'tests/e2e/wf4-local.py'),str(out/'mutant-server'),str(out/'run')];p=subprocess.run(cmd,capture_output=True);(out/'runner.stdout').write_bytes(p.stdout);(out/'runner.stderr').write_bytes(p.stderr);(out/'report.json').write_text(json.dumps({'mutation':'remove task and step visibility guard','command':cmd,'binarySha256':hashlib.sha256((out/'mutant-server').read_bytes()).hexdigest(),'exitCode':p.returncode,'detected':p.returncode==1,'decision':'retain task and step scope visibility'},indent=2));assert p.returncode==1
