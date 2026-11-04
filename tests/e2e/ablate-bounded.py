#!/usr/bin/env python3
"""Build isolated removal candidates; run the ordinary behavioral runners on their binaries."""
import pathlib,sys,subprocess,shutil,json,hashlib,os
root=pathlib.Path(__file__).resolve().parents[2];out=pathlib.Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True);env=os.environ|{'GOWORK':'off','GOTOOLCHAIN':'local','CGO_ENABLED':'1'};go=pathlib.Path(env['GOROOT'])/'bin/go';selected=set(sys.argv[2:]);rows=[]
def body(text,signature,replacement):
 start=text.index(signature);opening=text.index('{',start);depth=1;end=opening+1
 while depth:
  if text[end]=='{':depth+=1
  elif text[end]=='}':depth-=1
  end+=1
 return text[:opening+1]+'\n'+replacement+'\n'+text[end-1:]
variants=['plain-scalars','invocation-views','compiled-schemas','schema-lifetime','catalog-index','expr-formatter','expression-depth','process-output-budget','process-tree-cleanup']
for name in variants:
 if selected and name not in selected:continue
 dest=out/name;copy=dest/'source';copy.mkdir(parents=True,exist_ok=True)
 for folder in ['cmd','internal','compat','schemas','vendor']:shutil.copytree(root/folder,copy/folder,dirs_exist_ok=True)
 for file in ['go.mod','go.sum']:shutil.copy2(root/file,copy/file)
 if name=='plain-scalars':path='internal/analysis/schema.go';change=lambda s:body(s,'func plainString(', 'return false')
 elif name=='invocation-views':path='internal/analysis/invocation.go';change=lambda s:body(s,'func WithInvocation(', 'return assets.WithDocumentReuse(ctx)')
 elif name=='compiled-schemas':path='internal/assets/schema.go';change=lambda s:s.replace('if cached, ok := compiledSchemas.Load(file); ok {','if cached, ok := compiledSchemas.Load(file); false && ok {').replace('actual, _ := compiledSchemas.LoadOrStore(file, schema)\n\treturn actual.(*jsonschema.Schema), nil','return schema, nil')
 elif name=='schema-lifetime':
  path='internal/assets/schema.go'
  def change(s):
   return s.replace('\n\t"sync"','').replace('documents map[string]map[string]any','documents map[string]map[string]any\n compiled map[string]*jsonschema.Schema').replace('&requestSchemas{documents: map[string]map[string]any{}}','&requestSchemas{documents: map[string]map[string]any{}, compiled: map[string]*jsonschema.Schema{}}').replace('var compiledSchemas sync.Map // embedded filename -> *jsonschema.Schema','').replace('if cached, ok := compiledSchemas.Load(file); ok {\n\t\treturn cached.(*jsonschema.Schema), nil\n\t}', 'cache, _ := ctx.Value(documentKey{}).(*requestSchemas)\n if cache != nil && cache.compiled[file] != nil { return cache.compiled[file], nil }').replace('actual, _ := compiledSchemas.LoadOrStore(file, schema)\n\treturn actual.(*jsonschema.Schema), nil','if cache != nil { cache.compiled[file] = schema }; return schema, nil')
 elif name=='catalog-index':path='compat/catalog.go';change=lambda s:s.replace('sync.OnceValues(func() (map[string]catalogVersion, error) {','func() (map[string]catalogVersion, error) {').replace('})\nvar catalogHash','}\nvar catalogHash')
 elif name=='expr-formatter':path='vendor/github.com/expr-lang/expr/file/error.go';change=lambda s:s.replace('var indLine strings.Builder\n\t\tindLine.WriteString("\\n | ")','var indLine = "\\n | "').replace("indLine.WriteByte('.')",'indLine += "."').replace("indLine.WriteByte('^')",'indLine += "^"').replace('srcLine += indLine.String()','srcLine += indLine')
 elif name=='expression-depth':path='internal/analysis/expressionreferences.go';change=lambda s:s.replace('if len(stack)-1 > 256 {','if false && len(stack)-1 > 256 {').replace('if recursive > 256 {','if false && recursive > 256 {').replace('if f.depth > 256 {','if false && f.depth > 256 {')
 elif name=='process-output-budget':path='cmd/argo-workflows-ls/tools.go';change=lambda s:body(s,'func (b *boundedOutput) Write(', 'return b.buffer.Write(p)')
 else:path='cmd/argo-workflows-ls/process_unix.go';change=lambda s:s.replace('return func() { _ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }, nil','return func(){}, nil')
 p=copy/path;original=p.read_text();mutated=change(original);assert mutated!=original,name;p.write_text(mutated);(dest/'mutation.json').write_text(json.dumps({'file':path,'originalSha256':hashlib.sha256(original.encode()).hexdigest(),'mutatedSha256':hashlib.sha256(mutated.encode()).hexdigest(),'original':original,'mutated':mutated},indent=2)+'\n')
 command=[str(go),'build','-mod=vendor','-trimpath','-buildvcs=false','-o',str(dest/'server'),'./cmd/argo-workflows-ls']
 with (dest/'build.stdout').open('wb') as so,(dest/'build.stderr').open('wb') as se:r=subprocess.run(command,cwd=copy,env=env,stdout=so,stderr=se)
 row={'variant':name,'command':command,'compiled':r.returncode==0,'exitCode':r.returncode}
 if r.returncode==0:row['binarySha256']=hashlib.sha256((dest/'server').read_bytes()).hexdigest()
 rows.append(row);(out/'build-results.json').write_text(json.dumps(rows,indent=2)+'\n');print(name,r.returncode,flush=True)
sys.exit(0 if rows and all(r['compiled'] for r in rows) else 1)
