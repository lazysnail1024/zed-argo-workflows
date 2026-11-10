# 当前实现

本文描述当前仓库源码。原生服务的 [VERSION](../VERSION)、adapter 的
[extension.toml](../editors/zed/extension.toml) 和它选择的
[native-server-version](../editors/zed/native-server-version)分别管理版本。
当前 native pin 为 `0.0.1-alpha.2`；修改 main 上的源码不会更新已发布的同名二进制。
安装该 pin 不能据此视为已包含本次索引重构，运行身份应以 `version --json` 的
`sourceCommit`、`toolkitVersion`、`catalogHash` 为准。

## 目标与产品边界

服务先识别属于 Argo Workflows 的 YAML，再为当前文件提供 schema、本地语义、补全、
悬停和符号查询；已知跨文件引用支持定义跳转和引用查找。证明输入完整、绑定唯一且位置精确后，
才提供 Rename 与安全 Quick Fix。当前文件不必等待整个父目录扫描结束。

支持的八种 CRD、精确版本边界及 `auto` 的整份版本 schema 选择见
[多版本支持](multi-version-support.md)。Workflow、WorkflowTemplate、
ClusterWorkflowTemplate、CronWorkflow 有工作流语义；其余四种提供 schema 检查。
服务做本地静态分析，不连接集群，不执行控制器或动态表达式。普通 YAML 的通用语法诊断由
通用 YAML 服务负责；Chart 的原始模板按路径识别并排除，只有显式 `render-check` 会调用 Helm。

## 模块划分

### 原生入口与运行支持

| 模块 | 当前职责与入口 |
| --- | --- |
| [cmd/argo-workflows-ls/main.go](../cmd/argo-workflows-ls/main.go) | 分派 `version`、`check`、`serve --stdio`、`explain-config` 等命令；CLI 组装报告并冻结终止状态，统一报告完整性和退出码 |
| [tools.go](../cmd/argo-workflows-ls/tools.go)、[Unix](../cmd/argo-workflows-ls/process_unix.go)/[Windows](../cmd/argo-workflows-ls/process_windows.go) 进程支持 | 显式 `render-check`、`lint-official`；子进程超时、合并输出预算及进程组/Job Object 回收 |
| [internal/workspace](../internal/workspace) | 产品配置、scope/target 选择、候选文件和 Helm 边界、稳定有界磁盘读取、CLI 一次性收集及请求输入预算 |
| [internal/fileuri](../internal/fileuri/fileuri.go) | 本地绝对路径和 file URI 互转；包含 Windows 盘符及转义边界 |
| [internal/buildinfo](../internal/buildinfo/info.go) | 原生版本、源码身份与 Go build metadata 中实际链接的 toolkit 版本 |
| [compat](../compat/catalog.go) | 只读嵌入 catalog，解析支持版本、GVK/schema 映射、上游来源与 catalog hash |
| [internal/assets](../internal/assets/schema.go) | 加载嵌入 schema；拒绝外部 schema 下载；缓存不可变编译结果，单许可限制冷编译并发 |

`internal/workspace/config.go` 管理 `.argo-workflows-ls.yaml`、默认命名空间、引用策略和
scope 的有效 target。`stream.go` 的 `Describe` 只读取配置元数据，`Candidate` 统一过滤
排除目录、嵌套 root、符号链接和 Helm 源文件，`ReadText` 执行单文件读取。
`disk.go` 仍服务 CLI 的一次性收集：先筛选 scope，再验证该文件的 target，不为未选中的
未分配文件验证无关默认值。`budget.go` 将所有 root/依赖闭包的文本记入同一请求预算。
磁盘读取会检查文件类型、读取前后身份、大小和时间；这能拒绝观察到的替换/并发写入，
并不宣称文件系统提供事务快照。[相关实现](../internal/workspace/stream.go)

### LSP 与持续索引

| 文件 | 所有权和职责 |
| --- | --- |
| [stdio.go](../internal/lsp/stdio.go) | JSON-RPC framing、能力协商、请求路由、UTF-16 更新、LSP document version 和 unavailable 状态；借用共享 Documents 的不可变 snapshot |
| [serve.go](../internal/lsp/serve.go) | 独占可变 session 和所有传输写入；最多一个前台请求、一个诊断发布 worker；取消、排队、按 URI 接受结果、epoch/proof 校验和 LSP 软内存目标 |
| [catalog.go](../internal/lsp/catalog.go) | 会话级 catalog、root/file 状态；原子接受输入并失效索引；WorkQueue 调度、两个文件 worker、`Index.Begin/Reuse/Commit`、依赖失效传播 |
| [catalog_discovery.go](../internal/lsp/catalog_discovery.go) | 配置刷新、可恢复 Scanner、目录优先级、Membership 扫描轮次、删除确认、scope 完整性与进度日志 |
| [catalog_inputs.go](../internal/lsp/catalog_inputs.go) | Documents read ticket、依赖闭包物化、逆向依赖、root/scope/file proof、按摘要查 workspace symbols、诊断完成确认 |
| [publication.go](../internal/lsp/publication.go) | 先判断资源归属并清空无关 YAML 的诊断，再执行本地/外部诊断；区分配置不可用、真实未完成和非本产品文件 |
| [document_workspace.go](../internal/lsp/document_workspace.go) | 为被 exclude 或内部目录隔离的 `opened:<URI>` buffer 重新获取当前输入；Quick Fix 不会误选磁盘 scope |
| [query_cache.go](../internal/lsp/query_cache.go) | 在获取当前输入后复用完整 scope 的最终查询结果；身份包含方法/参数、文本、配置及索引候选 generation |
| [edits.go](../internal/lsp/edits.go) | Rename/Code Action 协议，版本化 WorkspaceEdit，延迟 resolve 的 token、输入 fingerprint 与安全性复查 |

`initialize` 声明增量文本同步、UTF-16、补全、Hover、Definition、References、
DocumentSymbol、WorkspaceSymbol 和 workspace folder 变化；编辑能力还受客户端的
WorkspaceEdit 能力约束。具体协议分支以 [stdio.go](../internal/lsp/stdio.go) 和
[edits.go](../internal/lsp/edits.go) 为准，未声明的 LSP 功能不视为已实现。

### 工作流语义与编辑

领域代码位于 [internal/analysis](../internal/analysis)，文件按实际分析阶段协作：

| 文件组 | 领域职责 |
| --- | --- |
| [ownership.go](../internal/analysis/ownership.go)、[schema.go](../internal/analysis/schema.go) | 识别 API group/kind、多文档归属；将 schema 检查结果投射到源码诊断，区分 schema 缺失与分析失败 |
| [projection.go](../internal/analysis/projection.go)、[semantic_origin.go](../internal/analysis/semantic_origin.go)、[expansion.go](../internal/analysis/expansion.go) | YAML alias/merge 的有界投影、源位置来源及逻辑展开预算；保留精确/派生/共享来源差异 |
| [local.go](../internal/analysis/local.go)、[references.go](../internal/analysis/references.go)、[arguments.go](../internal/analysis/arguments.go)、[depends.go](../internal/analysis/depends.go) | 本地模板、参数和输出绑定，调用实参与 DAG/steps 依赖等静态规则 |
| [expressionreferences.go](../internal/analysis/expressionreferences.go) | 使用 Expr lexer/parser/AST 识别表达式引用和位置；不执行表达式，错误格式化补丁单独记录 |
| [external.go](../internal/analysis/external.go)、[external_exit.go](../internal/analysis/external_exit.go)、[inherited_parameters.go](../internal/analysis/inherited_parameters.go) | templateRef/workflowTemplateRef、命名空间、clusterScope、继承及调用方依赖，按 scope 处理不完整或歧义绑定 |
| [index.go](../internal/analysis/index.go) | 将当前资源投射成共享 `index.Facts` 与脱离原文的 DomainSymbol 摘要 |
| [queries.go](../internal/analysis/queries.go)、[query_context.go](../internal/analysis/query_context.go)、[query_schema.go](../internal/analysis/query_schema.go) | 未完成 YAML 的查询上下文、schema 分支筛选、字段补全与悬停 |
| [domain_queries.go](../internal/analysis/domain_queries.go)、[domain_external_completion.go](../internal/analysis/domain_external_completion.go)、[symbols.go](../internal/analysis/symbols.go) | 域内/跨资源名称补全、定义、引用和符号位置 |
| [edits.go](../internal/analysis/edits.go)、[quickfixes.go](../internal/analysis/quickfixes.go)、[localrename.go](../internal/analysis/localrename.go)、[pin.go](../internal/analysis/pin.go) | 源码编辑计划、YAML 标量编码、绑定安全性和版本 pin 修复；拒绝不精确或不安全的修改 |
| [invocation.go](../internal/analysis/invocation.go)、[preparse.go](../internal/analysis/preparse.go)、[facts.go](../internal/analysis/facts.go)、[external_facts.go](../internal/analysis/external_facts.go) | 单次分析复用、CLI 有界预解析、可选解析事实交接及外部诊断复用；缓存不改变输入准入或完整性判断 |

## 公共依赖与隔离边界

通过 [go.mod](../go.mod) 固定源码版本
`v0.0.0-20261110114800-b1e411d4b3ab`，完整 commit 为
`b1e411d4b3abe5c1ad70518be161c1daff717d61`。
[toolkit.lock.json](../contracts/toolkit.lock.json)记录源码/ZIP/合同 hash，`published: false`；
依赖方式是固定源码 commit 和已提交 vendor，无需 toolkit release、兄弟仓库 checkout 或本地 `replace`。
[toolkit-design-3.md](../contracts/toolkit-design-3.md)是按 hash 校验的 API 合同，不能当作旧设计删改。

| 共享能力 | 本仓落点 | 留在 Workflows 的决策 |
| --- | --- | --- |
| `source` | workspace、analysis、LSP 共同使用不可变 Snapshot/Revision/Span 和 UTF-16 映射 | URI 本地路径政策、LSP version、配置与资源限额 |
| `yamlview` | analysis 的语法视图、投影和位置查找 | 哪些 GVK 属于 Workflows、alias/merge 领域语义、schema 和诊断 |
| `edit` | analysis 产生编辑计划，LSP 输出 WorkspaceEdit | Rename/Quick Fix 可行性、候选唯一性、配置 identity 和客户端能力 |
| `workspace.Documents` | `newCatalog` 创建唯一实例；`session.accept` 在 catalog 锁内接受通知并同步失效；`readSnapshot` 使用 BeginRead/PublishRead | LSP document version、资源拒绝和恢复策略，当前输入的索引失效 |
| `workspace.Scanner/Membership` | `catalog_discovery.go` 有界枚举；open/change/close/watch 记录 observation，完整且当前轮次才能 sweep | root 归属、exclude、Helm、配置 epoch、scope 完整性与 Ready/Limited 判定 |
| `workspace.WorkQueue` | `catalog.go` 按 URI 合并优先级和运行中后续事件；worker 等待 Wake 并保证 Complete | 当前文件/相关目录优先级、具体任务内容、两个 worker 与各项预算 |
| `index.Index` | `analysis.Summarize` 产出符号、引用、未解析依赖和 digest；catalog 管理票据与查询 | key 编码、namespace/kind、依赖闭包、缺失/歧义语义和最终发布 proof |

依赖方向是 Workflows → toolkit 公共包。toolkit 不回调 Workflows 的领域逻辑，不依赖 LSP、
Zed、Argo schema 或这个仓库；Workflows 与 Argo CD 插件互不 import。
两者可以使用相同共享源码，但各自拥有独立文档状态、索引、配置、资源判定、schema 和发布周期。
Expr 与 JSON Schema 是本仓实际使用并单独记录补丁的依赖，不因使用 toolkit 而隐去其来源。

## 输入、扫描与查询如何协作

1. **输入有唯一权威。** 已打开文档的文本、revision、读票据及总 open bytes 归 Documents。
   LSP buffer 只保留借用 snapshot、协议 version 和 unavailable；不同 revision 的计数和 LSP version
   不混用。同一 didChange 中的 UTF-16 edits 按顺序解释，整体成功后才提交；旧 version 或非法范围不覆盖当前输入。
2. **失效先于异步任务。** `session.accept` 在同一锁内更新 Documents 和 catalog。
   接受文件变更或发生资源/容量拒收时推进 stamp 并撤销旧位置；worker 提交需核对当前 root 对象、epoch 和 stamp。
   旧 version 和非法范围更新被忽略，不属于这类已接受变更。
   删除 workspace root 只撤销索引成员资格，仍打开的 buffer 继续由 Documents 保存。
3. **当前文件先响应。** 当前 buffer 的本地诊断独立于全目录扫描；当前文件/相关目录提升优先级。
   关闭文件的前台请求只等待 root 配置元数据，随后读取所需文件，不等待全量发现。
   后台 catalog 使用会话 context，文件任务与前台请求分别受五秒 deadline 约束。
4. **扫描可继续。** `Scanner.Next(ctx, 64)` 每次最多 64 个工作单位（包括目录打开和非 YAML 项），
   context 预算为 50 ms，不能硬中断阻塞的 OS 调用；每 root 最多 64 条目录提示。
   批次的路径先被接纳或记为失败，才公开 Done。WorkQueue 保管 ready、overflow、running 和合并后续任务，
   不另存一份 discovery backlog，也不每 10 ms 遍历全部文件补队列。
5. **只有完整扫描能确认缺失。** Membership 的 observation 覆盖扫描期间的 open/change/close/watch；
   必须仍是当前 root/epoch，且 scanner 成功 Complete，才能清除未见文件。
   scope A 的文件失败可以只影响 A；枚举失败或成员 scope 尚未知会影响整个 root 的完整性。
6. **前台只取需要的源码。** 正向闭包用于定义和普通调用方诊断；References/安全编辑按需要加入逆向依赖。
   scope 尚未完整时可返回已知正向候选。领域分析对未证明的缺失保留 unresolved 的 Information。
   确认资源缺失为 Error 要求 closed 策略、完整 scope 和已知 namespace/cluster 身份；
   References 要求所选 scope 完整且本地/外部合并绑定唯一，安全修改也要重新证明完整性与绑定。
   `opened:<URI>` 的隔离 scope 不会被重新绑定到另一个磁盘 scope。
7. **错过 watcher 也会收敛。** 一轮结束约 30 秒后开始下轮枚举并读取关闭文件，内容相同复用摘要，
   同 size/mtime 的字节变化也能被发现。查询重新获取已知依赖；完全未知的新文件由维护扫描发现。
   维护扫描本身有 I/O，不承诺无成本。
8. **发布和查询分别确认。** 只有接受的诊断 publication 能确认外部诊断已完成；普通 query 不修改此状态。
   本地诊断先发后，scope 完整时补发外部诊断。结果带 root epoch、scope generation、file stamp/hash，
   失效后不能发布旧位置或恢复旧 Quick Fix；无变化的 Ready→Ready 不反复刷新全部已打开文件。

这些行为的直接依据是 [catalog 三文件](../internal/lsp/catalog.go)、
[扫描实现](../internal/lsp/catalog_discovery.go)、[输入与 proof](../internal/lsp/catalog_inputs.go)
及 [catalog 测试](../internal/lsp/catalog_test.go)。

### 资源索引表达什么

资源 key 为 `[kind, effective namespace, name]`，scope 使用配置 target ID。
ClusterWorkflowTemplate 不带 namespace；一个 YAML 文件可以有多个资源。
未找到的静态 target 仍进入依赖集合，后续创建、删除或变成歧义时可让调用方失效。
精确的独立标量记录可跳转 span；alias/merge 等派生或共享来源保留语义依赖，不伪造编辑位置。

digest 包含资源 spec 和 target/namespace/reference policy。仅注释变化可以更新位置而不改变语义签名；
模板参数、输出、继承等变化会沿依赖传播，并按 key/URI 去重。
`Index.Reuse` 仅在当前票据下、字节和有效配置未变时恢复 pending facts；旧位置在恢复/重建前不可见。
catalog 不另存 previous facts 副本。索引本身不证明目录完整，也不替插件解释 key。
[摘要实现与测试](../internal/analysis/index.go)、[行为测试](../internal/analysis/index_test.go)

## 预算与生命周期

| 项目 | 当前上限或生命周期 |
| --- | --- |
| 单资源文本 / 配置文本 | 5 MiB / 1 MiB |
| 权威 open text / 每个请求保留的全部输入文本 | 各 128 MiB；请求先预留所有 open buffers，再计入关闭依赖；借用 open snapshot 不重复收费 |
| catalog 文件 / Documents entries | 100,000 / 100,002 |
| 索引符号 / 引用与依赖 / 逻辑字符串 | 1,000,000 / 2,000,000 / 128 MiB |
| 域符号摘要 | 总量 64 MiB、1,000,000 项；单文件构造 8 MiB |
| WorkQueue | ready 512；outstanding 100,002，含 overflow/running；文件 worker 2 |
| CLI 一次性目录收集 | 最多 50,000 YAML 候选；五秒分析 deadline |
| 请求内解析复用 / 可选跨 worker 解析事实 | 50 MiB / 8 MiB 文本和 512,000 节点；超出可选缓存不扩大输入预算 |
| 外部诊断事实复用 | 最多 20,000 keys / 4 MiB；候选集合参与身份 |
| 名称候选 / workspace symbols | 1,000 / 20,000；冷索引或达到上限时不承诺全量结果 |
| 冷 schema 编译 | 一个可取消等待的许可；暖命中直接使用不可变缓存，失败不缓存 |
| LSP Go 软内存目标 | 288 MiB；保留更低的调用方限制，结束恢复原值；不等于进程 RSS 上限 |

关闭文件的完整原文不放 catalog LRU，临时 Workspace、解析 context 和 buffer map 随 worker 释放；
摘要名称、容器名和编辑字符串复制所需片段，避免小 substring 持有整份旧原文。
资源或容量拒收会标记 unavailable，不能退回旧 buffer 或磁盘并声称成功；完整替换或 close/reopen 可恢复，条目数耗尽也不会终止服务。
旧 version 或非法 UTF-16 更新则被忽略，已接受快照保持有效，这与资源拒收不同。
内存预算覆盖的是明确对象，C parser、编译临时图、嵌入数据和 Go heap headroom 仍需要实际进程测量。
[实现预算](../internal/workspace/budget.go)、[LSP 内存策略测试](../internal/lsp/memory_test.go)、
[CI 资源验收](CI.md)

## 构建、adapter 和发布隔离

| 边界 | 当前实现 |
| --- | --- |
| schema 生产 | [collect-catalog.py](../scripts/collect-catalog.py) 收集固定 upstream commit；[normalize-schemas.py](../scripts/normalize-schemas.py) 规范化、结构约束和内容去重；运行时只读取嵌入产物 |
| Go vendor | [vendor-native.py](../scripts/vendor-native.py) 恢复原生 parser 源码并验证 Expr/JSON Schema 补丁 hash；合同和 notice 随产物保留 |
| 原生交付 | [release.py](../scripts/release.py)、[package-native.py](../scripts/package-native.py) 产生可独立运行的原生包、身份与校验记录；[native.yml](../.github/workflows/native.yml) 验收，[release.yml](../.github/workflows/release.yml) 发布 |
| Zed 入口 | [adapter.rs](../editors/zed/src/adapter.rs) 转发设置并启动 `serve --stdio`；[distribution.rs](../editors/zed/src/distribution.rs) 选择显式路径→PATH→固定 release，管理下载和缓存；[identity.rs](../editors/zed/src/identity.rs) 校验协议及 release 身份 |
| Zed 交付 | 独立 Cargo crate、WASI 组件、包脚本及 [zed.yml](../.github/workflows/zed.yml)/[zed-release.yml](../.github/workflows/zed-release.yml)；不构建 Go，也不包含领域分析或 schema |

原生构建不 import Rust/Zed 或读取 adapter 版本；adapter native pin 可以来自另一个原生源码 commit。
安装器在最终 `.gen2-*` 目录完成 hash 和 identity 验证后创建 `.complete/`，执行过的 EXE 不再被移动。
管理缓存每次使用都重新验证；细节和故障处理见 [分发与安装](distribution.md)。
[tests/native_isolation.py](../tests/native_isolation.py) 与 adapter 隔离测试检验这条边界。
验收分 Go 单元/race、真实 CLI/stdio、资源负载、WASI 安装运行时和真实 Zed GUI；
各自可证明的内容及命令统一放在 [CI](CI.md)，不把自动化通过写成 GUI 验收结论。
