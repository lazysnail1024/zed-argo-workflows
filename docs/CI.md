# 自动化验收

本页描述当前可执行门禁。某个候选是否通过，应查看该源码 commit 对应的完整运行结果；
文档中的门禁配置本身不等于验收通过。原生、adapter 与真实 Zed GUI 的证据分别记录。

## Workflow 与职责

| 入口 | 执行范围 |
| --- | --- |
| [native.yml](../.github/workflows/native.yml) | 五个平台实际构建/执行 Go 服务；Linux 语义、资源与 mutation；汇总 `Acceptance gate` 并校验完整原生分发 |
| [zed.yml](../.github/workflows/zed.yml) | Linux/macOS/Windows 的 Rust 安装器测试；Linux/Windows 的 WASI 安装运行时；独立 WASM、package 和隔离负向控制；汇总 `Zed acceptance gate` |
| [zed-install.yml](../.github/workflows/zed-install.yml) | 三个操作系统使用已发布 Zed adapter `0.0.1-alpha.4`（固定原生 LSP `0.0.1-alpha.3`）做真实 Release 文件系统安装、替换、回滚和校验 |
| [release.yml](../.github/workflows/release.yml)、[zed-release.yml](../.github/workflows/zed-release.yml) | 对固定源码和版本运行各自门禁后发布；发布规则见 [分发](distribution.md) |

原生支持 Linux amd64/arm64、macOS amd64/arm64、Windows amd64；Go 版本来自
[go.mod](../go.mod)，CGO parser 要求 C 编译器。Rust 使用 adapter 的固定工具链和 `--locked`。
原生 workflow 不构建 Rust、不依赖 editor checkout；adapter workflow 不构建 Go。
验收 workflow 使用只读权限，发布 job 单独声明所需的写入权限。固定 action commit、
禁用持久 checkout 凭据、命令/job 超时与并发取消规则均由实际 YAML 定义。
必须任务失败、取消或跳过不会通过 aggregate gate；
是否把 gate 设为合并必需项由仓库 branch rules 决定。

## 原生门禁

| 门禁 | 当前检查内容 |
| --- | --- |
| Native matrix | vendor 原生构建、`go vet`；analysis/assets/lsp/workspace 单元测试；实际 CLI/stdio 消费者、版本矩阵、原生打包与 release staging |
| Linux race | 同一组 Go 包的 `-race`，覆盖 catalog 生命周期和共享文件权威 |
| Linux semantic，三个 shard | 现有模板/引用/继承/DAG/编辑/生命周期测试，来源资产，固定且校验 hash 的 Argo/Helm 工具；真实 schema 与合成查询 fixture 分开记录 |
| Linux resources：adversarial | aggregate input、表达式、alias/merge、buffer/snapshot、子进程输出上限、取消与后代回收 |
| Linux resources：load | 10,000 文件/50 MiB；CLI 完整计数、冷启动、活跃诊断、查询、600 次持续编辑、10,000 次 open/edit/close 与内存保留 |
| Linux resources：index | 每个 10,000/50,000 WorkflowTemplate 规模五个新进程，每文件 4 KiB padding；完整首轮/维护轮次、当前文件响应、漏事件收敛与 RSS |
| Linux resources：queries | 完整候选集/继承参数/exit-role 的 100 ms 请求预算；既有冷 publication/definition、单 scope 负载与真实进程内存保留 |
| Mutation：core/audit/contracts/legacy/resources | 同源通过基线后，验证指定语义、publication/cache 或资源保护移除会触发对应断言 |
| Complete native distribution | 五平台原生身份、源码、校验和、原始 license/provenance 与包库存完整性 |

精确消费者清单由 [tests/ci/run.py](../tests/ci/run.py) 的 `NATIVE`/`EXTENDED` 维护；
资源清单由 [resources.py](../tests/ci/resources.py) 维护，不复制一份易过期的测试计数。
当前 3.5.0 与 3.6.0 都是受支持的精确 target；混合 scope 负载要求完整计数、正确版本、
无实验性标记及实际期望的诊断/退出码。

### 多版本完整性

每个平台实际消费当前离线目录的 146 个版本、1,052 个版本/资源对。
四个串行 shard 分别包含 37/37/36/36 个版本，每条命令仍有 300 秒上限；
单版本原生进程 watchdog 为 20 秒，产品分析 deadline 为五秒。
聚合器逐版本核对顺序、集合、hash、计时、原始 CLI 报告和退出状态；
最后 shard 保留非矩阵的 CLI/LSP、CEL 规范化和缺失 kind 断言。
[版本消费者](../tests/e2e/wf-multi-version.py) 和
[聚合器反例自测](../tests/ci/test_multi_version.py) 同时必需。

### 持续索引与证据边界

[wf-index-boundaries](../tests/e2e/wf-index-boundaries.py)、
[wf-mixed-parent-index](../tests/e2e/wf-mixed-parent-index.py)、
[wf-persistent-workspace](../tests/e2e/wf-persistent-workspace.py) 和
[wf-persistent-slowio](../tests/e2e/wf-persistent-slowio.py) 进入 native suite。
它们验证已知变化立即失效、未知无 watcher 文件经真实维护发现、准确最终位置、
源签名变化、当前 buffer 优先与超五秒 discovery 继续运行。
慢 I/O 消费者单独构建有身份记录的读取延迟 overlay，不放宽生产 deadline。

[wf-symbol-query-budget](../tests/e2e/wf-symbol-query-budget.py) 进入 native suite：
10,001 个真实文件产生 20,002 个模板/参数展示符号，验证稳定的前 20,000 项、末尾低命中、
零命中，以及取消突发与当前文件 didChange/hover 并发后的正确位置、单次终态和恢复；
active/queued workspace 查询不得交付编辑前的位置，恢复后检查 workspace 与当前文档的相同位置。
`catalog_symbols_test.go` 另以可控小预算验证零命中计费、锁等待取消、分页身份移除与旧位置拒绝；
`index_queries_test.go` 验证多个候选查询共享请求预算。
`catalog_query_race_test.go` 用真实 Documents/WorkQueue/Index 票据与显式屏障复现双向读取交错：
旧 worker 的被动重排不能撤销新读取，新的字节观测必须撤销旧解析，真实后续 watch 即使同字节
也必须拒绝旧 proof。同 URI 只串行读取/发布窗口；取消、其他 URI、删除重建与 didOpen 独立验证。
audit mutation group 同时移除正确的输出上限、注入分页漏项，要求上述真实消费者报告指定语义失败，编译失败或超时不能算通过。
这些测试不替代资源 suite 现有的 10,000 文件/20,000 符号五次冷索引验收。

`internal/lsp/catalog_test.go` 的正向用例覆盖共享 Documents、队列及完整扫描的接入。
[ablate-shared-lifecycle.py](../tests/e2e/ablate-shared-lifecycle.py) 的七项控制由 audit mutation lane 调用；
必须先看到所有指定 Go 测试通过，再以实际测试名和断言消息确认故障，panic 或编译失败不能算检测。

三个 density 消费者在计时第一条查询前，被动观察当前 root、精确文件数、空队列及当前打开文本
字节数匹配的 `Ready` 回执；第一条诊断通知可能只有本地分析，不能当成完整索引证明。
外部歧义场景还要求实际的跨文件诊断；无诊断场景分别记录当前版本诊断与索引就绪，不把空数组
声称为完整诊断 worker 的完成证明。原查询列表、精确返回内容及每请求 100 ms 保持原值；
不发送预热查询、不重试失败查询，缺就绪回执会超时失败。CLI 完整性与计数继续单独断言。
[就绪观察](../tests/e2e/query_density_support.py)、[密集候选](../tests/e2e/wf-external-candidate-density.py)

100 scope 的保留测试同样先被动观察 10,000 文件、空队列和零打开字节的初始 `Ready`，
再执行原来的 20/100/20 阶段、140 对请求与 1.10 增长比例。索引就绪的 60 秒包含 initialize，
原有 RSS 采样覆盖这段等待，等待期间样本仍计入峰值；没有额外查询或调整中位数窗口。
因此对比的是索引完成后的查询保留，不把目录尚在增长的中间状态当成稳定基线。
父级同时核对四个消费者使用的协议 helper 和就绪 helper 的源码 hash。

## 全部测试家族与正交分工

[完整入口清单](../tests/ci/inventory.json) 为每个 first-party Python、Go 与 Rust 测试文件记录
角色、现行 gate、调用路径和行为合同。`test_inventory.py` 在构建前扫描实际文件；新增、删除或改名
而未分类的入口会直接失败。helper、fixture builder 和兼容别名有独立角色，不能计为行为用例。
该清单描述执行路由；某次运行是否通过仍以那个源码 commit 的原始证据为准。

| 家族 | 行为 oracle | CI 路由及正交维度 |
| --- | --- | --- |
| Go analysis/assets/workspace | 实际 schema 缓存、索引预算、取消、输入流与版本身份 | 五平台单元测试，Linux 追加 race |
| Go LSP catalog/request | 可控调度下的旧票据、维护、readiness、共享文件权威和取消 | 五平台单元测试；Linux 定点移除 guard 验证断言 |
| CLI/stdio 平台合同 | 真进程退出、精确诊断、位置、编辑字节、取消终态和恢复 | 保留五平台现有 NATIVE 选择；不把全部 Linux 专项乘以五 |
| 版本与离线资产 | 146 个精确版本、1,052 个版本/资源对、原始 schema hash | 每个平台完整版本目录；分 shard 不削减版本覆盖 |
| 语义组合 | 作用域、继承、DAG、参数、外部引用、合法/非法目标和 YAML 拒绝 | Linux EXTENDED；每个场景控制其独立输入维度 |
| Schema 查询 | 真实版本 schema 和人工构造的边界 schema | Linux schema gate；两种 fixture 的二进制与结果分开 |
| 真实 Argo/Helm | 固定 hash 的实际工具、版本拒绝、render capability 和源文件归属 | Linux 集成合同；工具失败与语义失败分别记录 |
| 资源和进程树 | 明确受限的 CPU/RAM/swap、输入/输出边界、后代回收 | Linux adversarial；正边界与超一边界各自独立 |
| 大工作区与持续索引 | 完整文件/资源/符号数量、发布、维护、内存保留与实际请求延时 | Linux load/index；queries 追加原有密集候选与保留合同 |
| Mutation | 同源正常 baseline、唯一源选择器、完整案例和断言集合、指定失败 | Linux core/audit/contracts/legacy/resources；编译/异常/超时不算 kill |
| CI 自测 | 每次只破坏一个证据条件，其他字段保留可信通过 baseline | 构建前：空/缺行/重复/错 hash/缺 assertion/worker 缺席/OOM 回执反例 |
| 原生分发 | 实际解压、运行身份、源码/法律文件/平台库存及 checksum | 五平台包消费与聚合拒绝；Linux 原生源码隔离 |
| Zed Rust 安装器 | 注入 Host 的安装、身份、缓存、替换、回滚 | 三操作系统 Rust tests；无 GUI 覆盖声明 |
| Zed WASI / 文件系统 | 真实 guest 执行、native probe、HTTP 与句柄，以及固定发布包安装 | Linux/Windows WASI；三个操作系统真实 release 文件系统安装 |
| Zed package / snippets | 实际打包 bytes 与精确展开 snippet 由原生 CLI 消费 | package 的每种坏条件有正常 baseline；snippet 只移除一个 Workflow spec |

正交化不删除兼容性版本或平台合同。相同 baseline 可由同 suite/参数/环境的多个 mutation 共用；
每个 mutant 只修改一个 guard 或一项语义规则，随后仍要求相同完整案例集合。
密集候选、继承参数和 exit-role 的 100 ms 请求预算保留原始定义：从发送 LSP 请求到收到对应终态，
不把 fixture 生成或进程退出算入请求时间。语义正确而超预算仍是失败，不以总通过行数掩盖性能问题。

## 真实资源约束

[resources.py](../tests/ci/resources.py) 使用 systemd transient service，并在 service 内读取和验证
`memory.max`、`memory.swap.max`、`cpu.max` 与四 CPU affinity；在消费者退出后再次读取，
要求限制未变化且 `memory.events` 的 `oom`/`oom_kill` 均为零。缺少退出后回执或后代被 OOM 杀死都失败。
构建在测量组外完成；
没有权限、cgroup v2 或足够 CPU 时，资源验收失败，不降级成无约束测试。

| 负载 | 环境与断言 |
| --- | --- |
| adversarial | 通常 512 MiB/no swap；合法精确 128 MiB snapshot case 保留独立 1 GiB 环境 |
| 固定 load / queries | 四 CPU、最多 8 GiB、no swap，消费者保持现有语义/延时/计数要求 |
| 持续 index | 四 CPU、8 GiB/no swap；10k/50k 的每轮收敛分别 60/300 秒，server RSS ≤ 512 MiB |

内存环境、逻辑输入限额、Go 软目标和原生进程 RSS 是不同指标。
测试同时保存它们；受约束的固定 fixture 通过不能外推成任意磁盘/编辑器的性能保证。
Linux FIFO、inotify、`/proc` 和进程组控制不代表 Windows/macOS 对应系统机制已被验证。

## 结果判定和防虚假通过

进程退出成功和语义证据必须同时满足。报告非空、预期 case 集合完整、`pass`/`passed` 和
所有 assertion 都是布尔 `true`；字符串、缺报告、空选择、prepare-only、异常与超时都失败。
普通消费者的 case 身份与 assertion 集合由父 runner 的
[contracts.json](../tests/ci/contracts.json) 独立选择，不能用成功前缀替代完整运行；资源样本同样核对固定选择。
资产版本来自 `compat/catalog.lock.json`，不能由子报告自行删减。
所有证据还必须匹配本次消费者与实际原生二进制的 SHA-256；已有输出目录不能复用。
family 测试验证子 worker 的结果，不能仅相信父进程记录。LSP 消费者有绝对响应 deadline，
取消要返回一次 `-32800`，无关消息不能延长等待。

[ablate-ci.py](../tests/e2e/ablate-ci.py) 使用同一个隔离源码构建基线和 mutant；
源选择器必须唯一匹配，mutant 必须成功编译，case/assertion 集合相同且指定语义断言改变。
编译错误、崩溃、timeout、空报告、缺测试或无关失败不算 mutation 检测。
历史 `ablate-*` 文件保留为当前严格 mutation runner 的薄兼容入口；已迁移的旧路径不重复计为覆盖。
其中 43 个历史 selector 归并为 38 项登记观察、37 种源代码故障配方；
模板/资源基数共用一个 guard 的两种独立资源场景，其余重复 selector 复用已有合同。
父 runner 从当前 registry 独立取得完整变体集合，子 driver 自报的成功前缀不能通过。
`query-snapshot-bytes` 直接使用 `facts_store_test.go` 中同 URI、同 revision、不同字节的
独立边界；外层 catalog 的 hash/revision 校验不能让旧 LSP 失效路径冒充该 guard 的证据。
该变体分别编译同源正常和单故障 Go 测试二进制，以结构化事件核对精确测试/子测试选择、
正常 PASS 和指定断言 FAIL；编译失败、panic、timeout、缺测试或额外选择均不算 kill。
原生测试进程单独执行并保留退出码，再把原始流转换为 JSON；转换器不能把信号或退出码 2
归一成普通断言失败。只有 `OutputType=error` 的指定断言计入检测，普通同文日志不算。
原来的 23 项 `wf-query-fact-handoff` CLI/LSP 用例仍由 semantic lane 独立执行。
过去只编译优化候选的 bounded 入口现在运行实际进程输出与子进程回收合同；
依赖历史源码包并把任意 signal 当作检测的 toolkit 入口改为当前 256 层 YAML 的边界控制。
源码修改只发生在临时副本，发布依赖和产品预算不变。

报告冻结、非 Workflow 归属、维护 publication、精确编辑字节和候选 generation 等教训，
以及各自的回归测试，统一见 [经验教训](lessons-learned.md)。

## 打包身份与验收留证

release-mode build 从精确 commit 的临时 worktree 构建，防止本地 ignored Go/C/embed 文件混入。
二进制 Go VCS metadata、源码 commit、catalog hash、构建模式和原始 dependency/schema/runtime
法律文本都必须一致。包消费测试实际解压并运行 native identity，独立检查文件库存、hash、权限和拒绝路径。
这是来源校验，不是声明 Go 二进制可逐字节复现。

各 job 以 `always()` 上传 `artifacts/ci/` 等证据，通常保留 30 天：原始 stdout/stderr、
命令/timeout 回执、JSON 断言、协议流量、源码/二进制 hash、工具链、实际资源限制和产物。
job 在上传前被终止时不能承诺完整 artifact；该次 GitHub job log 仍需检查。

## 运行入口

使用固定 Go/Rust、Python 与 C 编译器，并为每次运行选择新的输出目录。
下面是原生构建和常用语义入口；Windows 使用 `.exe` 文件名。

```sh
python tests/ci/run.py build --output artifacts/ci/build
python tests/ci/run.py suite --suite native \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/native
python tests/ci/run.py package \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/package
python tests/ci/install-tools.py --output artifacts/ci/tools
python tests/ci/run.py suite --suite extended --tools artifacts/ci/tools \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/extended
python tests/ci/run.py schema \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/schema
python tests/ci/run.py mutations --group core --output artifacts/ci/mutations-core
python tests/ci/run.py mutations --group audit --output artifacts/ci/mutations-audit
python tests/ci/run.py mutations --group contracts --tools artifacts/ci/tools --output artifacts/ci/mutations-contracts
python tests/ci/run.py mutations --group legacy --output artifacts/ci/mutations-legacy
python tests/e2e/ablate-shared-lifecycle.py artifacts/ci/shared-lifecycle
```

Linux 资源入口额外需要 systemd、cgroup v2、四 CPU 和非交互 sudo：

```sh
python tests/ci/resources.py --suite adversarial \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/adversarial
python tests/ci/resources.py --suite load \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/load
python tests/ci/resources.py --suite index \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/index
python tests/ci/resources.py --suite queries \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/queries
python tests/ci/run.py mutations --group resources --output artifacts/ci/mutations-resources
```

Rust/WASI/package 入口见 [开发指南](../DEVELOPMENT.md)。需要交付身份的构建必须从干净 commit
按 workflow 传入 release version；普通开发构建不因此变成可发布 release。

## 与真实编辑器验收的关系

Native CI 证明实际 CLI/stdio 和包行为；原生 Rust 测试证明注入 Host 边界下的安装协议；
WASI 消费者证明实际安装代码、原生 identity probe 和受控 Windows 文件句柄的交互；
文件系统安装测试证明安装/回滚过程。它们都不启动完整 Zed GUI。

真实编辑器验收需要记录 Zed/OS、adapter/native 身份、设置和日志，并观察首次下载、
离线缓存启动、诊断/补全/跳转、允许和拒绝的 Rename、Quick Fix/undo、配置恢复和
YAML/Helm 服务共存。详细使用和排障见 [分发与安装](distribution.md)。
