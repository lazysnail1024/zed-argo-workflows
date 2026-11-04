#!/usr/bin/env python3
import pathlib,tempfile,shutil,subprocess,os,json,hashlib
root=pathlib.Path(__file__).resolve().parents[2];out=root/'artifacts/wf2-ablation';out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='wf2-ablation-') as d:
 copy=pathlib.Path(d)
 for directory in ['cmd','compat','internal']:shutil.copytree(root/directory,copy/directory)
 shutil.copy(root/'go.mod',copy/'go.mod');shutil.copy(root/'go.sum',copy/'go.sum')
 if (root/'vendor').exists():shutil.copytree(root/'vendor',copy/'vendor')
 p=copy/'compat/catalog.go';s=p.read_text();assert 'return fmt.Errorf("unknown target version: %s", version)' in s;p.write_text(s.replace('return fmt.Errorf("unknown target version: %s", version)','return nil'))
 env=os.environ|{'GOROOT':'/tmp/zed-argo-go-mod/golang.org/toolchain@v0.0.1-go1.27.3.linux-amd64','GOMODCACHE':'/tmp/zed-argo-go-mod','GOCACHE':'/tmp/zed-argo-go-build','GOWORK':'off'};cmd=[env['GOROOT']+'/bin/go','build','-buildvcs=false','-trimpath','-o',str(copy/'server'),'./cmd/argo-workflows-ls'];p=subprocess.run(cmd,cwd=copy,env=env,capture_output=True);(out/'build.stdout').write_bytes(p.stdout);(out/'build.stderr').write_bytes(p.stderr)
 assert p.returncode==0,'compile failure cannot detect behavior';shutil.copy(copy/'server',out/'mutant-server');cmd=[str(out/'mutant-server'),'check','--root',str(root/'tests/fixtures/schema'),'--format','json','--target-version','99.99.99'];p=subprocess.run(cmd,capture_output=True);(out/'mutant.stdout').write_bytes(p.stdout);(out/'mutant.stderr').write_bytes(p.stderr);v=json.loads(p.stdout);detected=not any('unknown target' in x for x in v['configurationErrors']);(out/'report.json').write_text(json.dumps({'mutation':'remove exact target rejection','command':cmd,'binarySha256':hashlib.sha256((out/'mutant-server').read_bytes()).hexdigest(),'exitCode':p.returncode,'assertion':'unknown target must be a configuration error','detected':detected,'decision':'retain exact target check'},indent=2));assert detected
