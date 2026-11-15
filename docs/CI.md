# 自动化验收

本页描述当前源码的执行入口、证明范围和失败判定。某个候选是否通过，应查看该源码 commit
对应的完整 GitHub Actions 运行；门禁配置、测试清单和本地执行结果都不等于 hosted 验收通过。

## Workflow 与职责

| 入口 | 执行范围与汇总 |
| --- | --- |
| [native.yml](../.github/workflows/native.yml) | 五平台 Go 构建和实际执行，Linux race、语义、资源与 mutation；完整原生分发及 `Acceptance gate`。 |
| [zed.yml](../.github/workflows/zed.yml) | 三操作系统 Rust 安装器、Linux/Windows WASI runtime、独立 WASM/package 与隔离控制；`Zed acceptance gate`。 |
| [zed-install.yml](../.github/workflows/zed-install.yml) | 三操作系统使用固定已发布 adapter `0.0.1-alpha.4` 和其原生 pin `0.0.1-alpha.3` 做文件系统安装、替换、回滚及拒绝控制；`Installer acceptance gate`。 |
| [zed-released-install.yml](../.github/workflows/zed-released-install.yml) | 在同仓库 adapter 发布 workflow 完成后核对实际发布 commit、tag、版本和资产，在三操作系统安装该精确版本；`Published installation aggregate`。 |
| [release.yml](../.github/workflows/release.yml)、[zed-release.yml](../.github/workflows/zed-release.yml) | 精确源码和独立版本的发布门禁、不可变 tag 与资产发布；详见 [分发](distribution.md)。 |

固定发布包的回归 fixture 与本次实际发布后的验收是不同入口。后者仅在上游成功且身份匹配时
执行安装；上游失败、跳过安装或证据缺失均不能成为发布验收通过。以上安装入口不启动 Zed GUI。

原生矩阵为 Linux amd64/arm64、macOS amd64/arm64、Windows amd64。Go 版本来自
[go.mod](../go.mod)，CGO parser 需要 C 编译器；Rust 使用 adapter 的固定工具链和 `--locked`。
原生 workflow 不构建 Rust，adapter workflow 不构建 Go。验收使用只读权限，发布 job 单独
声明写入权限。固定 action commit、checkout 凭据策略、超时和并发取消规则以 workflow 为准。

## 变更选择与必需门禁

[selection.py](../tests/ci/selection.py) 仅在 PR 的完整源码 diff **全部为修改**
`README.md` 和/或 `DEVELOPMENT.md` 时选择文档 lane。`docs/**`、新增、删除、重命名、
混合路径、不可用或过大的 diff，以及非 PR 事件均选择完整计划。已知 checkout 与 event head
不匹配直接失败；缺少用于比较的 Git 对象按完整计划处理。
PR event JSON 统一按 UTF-8 读取，使不同操作系统使用相同的 base/head 和变更身份；
不能让本机默认字符编码把有效的中文事件内容变成缺失源码信息的兜底计划。

文档 lane 执行真实 `git diff --check` 和选择器控制，保留 source/run/attempt 身份及原始输出。
它明确排除产品 lane，但三个汇总 gate 仍运行。`Acceptance gate`、`Zed acceptance gate` 和
`Installer acceptance gate` 对照选定计划核验全部 job 状态与 receipt：选定任务缺失、重复、
失败、取消或跳过，以及被排除任务意外运行，都失败。文档 lane 成功只证明该文档检查。

计划、receipt 和 payload 的生产及复验入口是 [control.py](../tests/ci/control.py)。计划绑定
base/head/tested commits、完整 changed-path digest、选中 lane 和排除 job；测试 checkout
只能是 event head 或其精确双亲 test merge。仓库 branch rules 必须要求实际 hosted check
名称与 GitHub Actions 来源；本地 gate 不能证明远端保护规则生效。

## 构建复用与来源身份

普通 Linux release 由 `ordinary` producer 构建一次，Linux native、三个 semantic shard 和
四个 resource job 消费该精确 binary。producer manifest 记录 source tree、Go/C 环境、原生
headers、依赖、linker flags、产品版本、runner image、run ID 和 attempt。消费者使用 producer
job 提供的 manifest digest 验证后执行绝对路径；不能自行提供一份新 manifest 代替来源验证。
[身份实现](../tests/ci/build_identity.py)、[导入控制](../tests/ci/import_e2e.py)

仅普通 producer 使用按编译器和输入身份限定的 build cache。合成 schema、慢 I/O overlay、
mutation、race、离线分发及其他平台构建独立执行。部分 rerun 若继承前一次 attempt 的 producer
会被拒绝，需要重跑完整 producer/consumer 组。release-mode build 从精确 commit 的临时
worktree 构建，避免 ignored Go/C/embed 文件污染；这不宣称二进制可逐字节复现。

## 测试家族与执行边界

[tests/ci/inventory.json](../tests/ci/inventory.json) 为 first-party Python、Go 和 Rust 测试入口
登记角色、现行 gate、调用路径与行为合同。[test_inventory.py](../tests/ci/test_inventory.py)
扫描真实文件，新增、删除或改名却未分类会失败。helper、fixture builder、兼容入口不额外计为
行为覆盖；完整消费者选择以 [run.py](../tests/ci/run.py) 的 `NATIVE`/`EXTENDED` 和
[resources.py](../tests/ci/resources.py) 为准。

| 家族 | 验证内容 | 当前路由 |
| --- | --- | --- |
| Go analysis/assets/lsp/workspace | schema 与事实缓存、票据/输入权威、readiness、预算和取消 | 五平台单元测试；Linux 追加 race。 |
| CLI/stdio 平台合同 | 实际退出码、精确诊断、位置、编辑后字节、取消终态和恢复 | 五平台 native suite。 |
| 离线版本 catalog | 完整精确版本/kind 集合、原始来源 hash、自动兼容及 query 语义 | 每个平台完整版本消费者。 |
| Workflows 语义 | 模板/继承、DAG、参数、外部引用、局部和跨文件编辑、生命周期 | Linux extended 的三个 shard。 |
| Schema 查询 | 实际 CRD graph 与人工构造的 query 边界 | Linux schema gate，两类 fixture 分开记录。 |
| Argo/Helm 集成 | 固定 hash 的真实工具、版本拒绝、render capability、源文件归属和取消 | Linux 工具消费者；静态 Helm 导航进入 native suite。 |
| 资源与规模 | 输入/输出限制、后代回收、完整索引、延迟、内存峰值与保留 | Linux adversarial/load/index/queries。 |
| Mutation | 通过基线、唯一源选择器、成功编译及指定行为断言失败 | Linux core/audit/contracts/legacy/resources。 |
| CI 自测 | 缺行、重复、错误 hash、缺 assertion、源身份/receipt/payload 错配 | 构建前自测和实际消费者证据控制。 |
| 原生分发 | 实际解压与运行、来源/平台/版本、法律文件、checksum 和资产库存 | 五平台包消费及聚合拒绝；Linux 源码隔离。 |
| Zed 安装器 | Host 协议、真实 WASI/native probe、Windows 句柄与缓存激活 | 三系统 Rust；Linux/Windows WASI。 |
| Zed package/snippets | 包 bytes、来源身份、精确 snippet 展开由 native CLI 消费 | 独立 package 与负向控制。 |
| 已发布包安装 | 文件系统安装、校验、替换、失败恢复与回滚 | 固定 fixture 和精确本次 release 两个入口。 |

`wf-render-profiles` 在 extended suite 中使用两个固定真实 Helm major。它以独立
dev/prod/reverse values 预期、实际工具调用日志和完整 stdio 帧验证 profile 结果，
收集/resolve 零执行、配置/values/lifetime 过期拒绝与新动作恢复、scope 隔离、
showDocument 响应后的下一请求、取消后代回收和无延迟打开，以及失败库存的保留与
显式清理。实际执行前的拒绝消息须包含需要保存的 values 路径；运行中的自定义输出
目录被替换成源 chart 链接时，源字节必须保持不变，原始工具输出仍由失败响应保留。
缓存负例分别从真实生成的独立目录开始，避免被复制标记拒绝掩盖内容、
额外文件或符号链接保护。`wf-quickfixes` 的 native 消费者同时验证 v2 配置 pin 只改变
目标字段，保留 profile、values 顺序及重复项；对应行和 assertion 集合由 contracts
文件独立固定。生成目录的所有权检查不等同于 Windows GUI 验收或所有竞争时序证明。

同一完整基线可供相同 suite/参数/环境下的多个 mutation 复用；每个 mutant 只修改一个保护
或语义规则，仍核验完整 case/assertion inventory。跨平台合同与兼容性版本不会因分组而减少。

### 版本完整性

当前离线 catalog 包含 146 个精确版本和 1,052 个实际版本/kind 对。版本消费者在每个平台
使用四个串行 shard，分别包含 37/37/36/36 个版本；每条命令 300 秒、单版本原生进程
watchdog 20 秒，产品分析 deadline 仍为五秒。聚合器核对顺序、集合、hash、计时、原始 CLI
报告和退出状态，最后一个 shard 保留其他 CLI/LSP、CEL 及缺失 kind 断言。
[版本消费者](../tests/e2e/wf-multi-version.py)、[聚合自测](../tests/ci/test_multi_version.py)

`wf-compatibility` 进入同一 native runner/inventory/独立 assertion 合同，按资源验证 Schema
候选、已建模规则候选及交集，覆盖九组语义边界和两条回移分支、来源身份、真实 auto/exact
对照、动态/不完整 scope、原因归属、未保存 buffer 和旧动作拒绝。它不替代全 146 版本检查。
`compatibility-joint-intersection`、`compatibility-marker-backport` 和
`compatibility-incomplete-external` 三个 contracts mutation 必须让指定真实断言失败。
`check-modeled-rules.py` 在生成 schema 前以及该消费者中验证语义来源清单；相同 CRD hash
不能代替语义源码和调用条件审查。可选本地源文件校验核对精确 blob、函数和调用方 hash，
该证据仅覆盖声明的边界样本，不表示执行了所有版本的上游 controller。

### 索引与请求

`wf-reaudit-query-io` 保留原三个独立 inotify 读取合同，并检查默认关闭的实际工作 trace：
真实补全结果、冷 Schema 编译、恢复/合成解析、缓存命中之前的当前读取和解析、trace-off
等价性，以及取消回复到 worker 释放的不同时间。计数不是由输出命中率推算；原始 stderr
保留后台和取消/丢弃工作的独立归属。计时为描述性证据，不新增或放宽资源门限。
固定 warm 补全 fixture 另要求源解析不超过一次、总解析不超过三次，且投影不超过已测的六次；
选定关闭依赖仍由 inotify 独立要求每次读取一次。该工作量合同来自先冻结的实际计数基线，
不把结果缓存命中或恢复事实数当作解析减少。`ablate-query-facts.py` 的 `open-restoration`
只禁用打开文档的恢复调用，必须让该工作量断言失败，同时完成全部五行消费者并保留正确
候选、trace-off 等价性和读取约束；原有 `snapshot-bytes` 仍验证完整文本身份不能省略。

`wf-equivalence` 保留原九种历史，另由固定字面量 fixture 比较 incremental/fresh 的真实
版本化 Rename、补全和已 resolve Quick Fix 的完整应用结果。UTF-16 非 BMP 字符与 CRLF、
同名其他 scope、实际应用版本的诊断和精确引用均进入预期；九个篡改控制验证 applier 会拒绝
过期版本、错误路径、重叠/越界/代理对中间位置和错误替换字节。父级材料校验核对所有输入、
独立预期、实际应用 YAML 和原始 session 生命周期；一致性通过不单独证明工作量下降。

`wf-analysis-status` 通过真实 CLI/stdio 检查磁盘与未保存 buffer 身份、独立的文件/scope
完整性、空引用结果的可判定性、拒绝原因及打开受影响文件后的恢复、配置和 buffer 使旧动作
失效、只读客户端的可用性。它与现有 Rename/quickfix 消费者一起运行；请求 quickfix 的旧
消费者显式使用 LSP `context.only`，避免把只读解释 command 当成可应用编辑。

`wf-resource-rename` 与 `wf-reaudit-actions` 另以真实 stdio 请求验证未发送 watch/config
事件的磁盘配置变化：policy、scope、target 或无效键不能在后台 metadata 刷新前继续授权
实际 Rename/已 resolve Quick Fix。配置 overlay 仍优先于磁盘；恢复后的新请求必须生成
精确版本化编辑，实际应用后绑定或诊断正确。最终交付原生测试补充已构造结果后才改配置的
时序，原 epoch/proof 仍有效时也须拒绝；它不单独代替外部消费者的真实编辑验证。

持续索引消费者覆盖已知输入立即失效、漏 watcher 的维护发现、精确最终位置、buffer 优先及
超五秒 discovery 继续收敛；慢 I/O 使用单独有来源身份的读取 overlay。workspace-symbol
消费者检查 20,000 项输出边界、尾部低命中、零命中成本、并发编辑和取消后的恢复。
确定性 Go 测试覆盖同 URI 读票据交错、旧 worker、分页删除和跨 scope 预算。
[索引边界](../tests/e2e/wf-index-boundaries.py)、[持续 workspace](../tests/e2e/wf-persistent-workspace.py)、
[慢 I/O](../tests/e2e/wf-persistent-slowio.py)、[符号预算](../tests/e2e/wf-symbol-query-budget.py)、
[读票据交错](../internal/lsp/catalog_query_race_test.go)

密集候选、继承参数和 exit-role 消费者在第一条计时查询前被动观察当前 root、完整文件数、
空队列和当前打开字节的 `Ready` 回执；本地诊断不能代替完整索引证明。每请求 100 ms 从发送
到对应终态计算，同时核对精确内容；不通过预热查询、重试或快速错误满足延迟要求。
跨 scope 保留测试同样先确认索引就绪，再执行 20/100/20 阶段和原 1.10 增长比例，等待阶段
仍计入 RSS 峰值。[就绪观察](../tests/e2e/query_density_support.py)、
[保留测试](../tests/e2e/wf-query-handoff-retention.py)

## 真实资源约束

[resources.py](../tests/ci/resources.py) 使用 systemd transient service，在 service 内读取
并验证 `memory.max`、`memory.swap.max`、`cpu.max` 和四 CPU affinity，消费者退出后再次
读取；限制变化、缺少退出回执或 `memory.events` 的 `oom`/`oom_kill` 非零都失败。构建在
测量组外完成。缺少权限、cgroup v2 或足够 CPU 时不降级成无约束的通过结果。

| 负载 | 环境与主要断言 |
| --- | --- |
| adversarial | 通常 512 MiB/no swap；精确 128 MiB snapshot 正边界独立使用 1 GiB。输入、表达式、alias/merge、输出预算和取消后代回收。 |
| load | 四 CPU、最多 8 GiB/no swap；10,000 文件/50 MiB，CLI 完整计数、冷启动、诊断、查询、持续编辑和 open/edit/close 保留。 |
| index | 四 CPU、8 GiB/no swap；10k/50k 规模各五个新进程，首轮及维护收敛分别 60/300 秒，server RSS ≤ 512 MiB。 |
| queries | 四 CPU、最多 8 GiB/no swap；完整候选、继承、exit-role 的 100 ms 请求，冷 publication/definition 与跨 scope 保留。 |

逻辑输入限额、Go 软内存目标、cgroup 限制和实际进程 RSS 分别记录。固定 fixture 的通过
不能外推任意磁盘或编辑器性能；Linux FIFO、inotify、`/proc` 和进程组控制也不代表其他系统
机制已被验证。

## 证据与防虚假通过

普通消费者同时要求成功退出、非空且完整的预期 case 集合、精确布尔 `pass`/`passed` 和全部
assertion。期望集合由父 runner 的 [contracts.json](../tests/ci/contracts.json) 独立选择，
版本集合来自 catalog lock，不能接受子报告的成功前缀。字符串、缺报告、空选择、prepare-only、
异常和 timeout 均失败；源码和实际 binary hash 必须匹配，新输出目录不得复用。

[ablate-ci.py](../tests/e2e/ablate-ci.py) 在同一隔离源码上运行正常基线和单故障变体。源选择器
必须唯一匹配、编译成功、case/assertion 集合相同并出现指定语义失败；编译错误、panic、signal、
timeout、缺测试或无关失败不算 kill。薄 `ablate-*` 兼容入口调用当前严格 runner，不能重复算覆盖。
`query-snapshot-bytes` 使用 [facts_store_test.go](../internal/analysis/facts_store_test.go)
的同 URI/revision、不同字节边界；真实测试退出码先保留，再转换结构化事件，只有指定 assertion
错误能证明检测。相关 CLI/LSP 事实交接消费者仍独立执行。

### 原始协议、输入与生命周期

`wf3-lsp` 与 `wf-equivalence` 保留 raw stdio、解析 traffic、完整输入快照和进程生命周期。
[materials.py](../tests/ci/materials.py) 从 checkout 的 fixture/expected hash、父合同输入和原始
LSP frames 独立复验，receipt 生成和下载后的 aggregate 都调用它。session helper 只负责
framing、deadline、有界 transcript 与进程清理，行为期望留在消费者及 fixture。

生命周期 oracle 从匹配的诊断 publication 开始，观察到下一次相关文档/配置/root 失效；
最终 close 阶段一直观察至 shutdown 和排空 EOF。区间内所有当前版本 publication 必须一致，
旧版本不当作当前结果；sleep 和无关响应不能充当完成屏障。下载后的 verifier 从原始事件重建
期望和阶段边界，不只相信子进程给出的 timeline。两 root 等价性同时要求第二 root 发现及
各 root 的精确 Definition URI/range，新进程重建相同 roots 与最终 overlay。

Linux native lane 在真实普通 binary 上执行
[material_effectiveness_e2e.py](../tests/ci/material_effectiveness_e2e.py) 和
[oracle_effectiveness_e2e.py](../tests/ci/oracle_effectiveness_e2e.py)。代理转发实际协议，只注入
指定的新/旧诊断、root-add 遗漏或错误 root 位置；协议正确性及消费者拒绝能力分别验证。

### 上传与下载后复验

成功 native lane 将完整 evidence 转移到一个 receipt payload，包含隐藏配置输入；失败 lane
保留原 evidence。只过滤已知 compiler/interpreter scratch，不删除语义输入。receipt 原有的
文件字节选择规则不变：被选中的 file symlink 在任何 referent 移动前先稳定读取，核对原
evidence digest 和 link/target 身份，再物化成独立普通文件。选中后变悬空、改指向或变成
非普通文件会失败；原规则没有选中的目录链接、FIFO 等不扩大为读取输入。这是原有
regular-byte payload 的交付，不承诺保存整个消费者工作目录的所有文件系统元数据。

[transport.py](../tests/ci/transport.py) 将完成的 lane receipt 和 payload 打包成自包含的
内容寻址 ZIP。同一收据中相同 SHA-256 的字节只存一份，lane receipt、producer manifest
和所有子 JSON 均作为原始字节保存，不重新序列化或改写原命令路径。传输清单记录逻辑路径、
类型和权限，普通文件另记大小与内容 hash。恢复为新目录中的独立普通文件；相同内容但不同权限
的文件仍独立创建，修改其中一份不会改动其他文件。没有 producer 外部对象依赖。

内层 ZIP 固定使用压缩级别 6；normal receipt artifact 只上传这个 ZIP，外层上传压缩级别
为 0，避免重复压缩。ordinary producer 的普通 artifact 使用压缩级别 6；release archives
与 ordinary imports 的身份验证和消费者不变。打包或上传失败时，独立的
`raw-native-receipt-*` artifact 保留已经移到 `artifacts/receipt` 的原始材料和传输指标，
原失败路径也保留尚在 `artifacts/ci` 的材料。raw fallback 不进入正常 acceptance 的
`native-receipt-*` 下载选择，不会被当作成功传输悄悄接受。
保留步骤用目录存在性选择原始或已移动材料，不使用 `hashFiles` 预读整个失败输入树。
大目录中的原始输入和隐藏文件仍由上传步骤完整处理；实际归档、receipt 和 gate 的内容
hash 校验保持不变，缺失材料仍不能形成成功的验收结果。

五个平台均执行 `transport_e2e.py --producer-only`：真实创建并重写收据、日志、配置和
可执行文件后运行 pack，独立读取 ZIP 验证原始字节、内容 hash 和 mode。固定 Windows
Python 3.14.0 还必须实际观察到 path/fd ctime 不同而 birthtime 相同；跨 API 身份比较
使用显式 birthtime，同一 API 在读取前后的完整 ctime/mode 检查仍保留。完整 hydrate、
路径与来源变化负向控制继续在 Linux 执行。

打包拒绝物理 symlink、特殊文件、输入变化及不安全路径。解包先验证严格 JSON、清单/对象
集合、ZIP 物理头、类型和便携路径别名，再有界读取压缩流并逐对象核对实际大小与 hash；
不信任 ZIP 声明的解压大小来截断验证。上限为 1,000,000 个逻辑项、单文件 4 GiB、逻辑字节总量 64 GiB、
清单 128 MiB、中央目录 256 MiB，以及路径 4,096 UTF-8 字节/128 层。恢复拒绝已存在的
目的地及 symlink 路径，使用持有的目录描述符创建文件。`pack` 面向各 native producer；
`hydrate`/`hydrate-set` 要求 POSIX descriptor-relative no-follow 操作，当前 acceptance
在 Linux 运行，本地控制也不等于其他平台的实际 CI 验收。

`hydrate-set` 先验证整套 artifact 目录及 lane 唯一性，再把每个 lane 恢复到独立新目录，
复核预检身份和最终输入集合。唯一 raw 兼容形式是原 docs-only 的
`docs.json` + `payload-docs` 布局，仍由原 gate 验收。混合 raw/CAS、额外文件或重复 lane
均拒绝；传输清单和指标始终留在 gate 收据树之外。失败可能保留排查用 partial 文件，
后续 gate 只在 hydrate 成功后执行，不以目录存在作为成功信号。

下载全部选中 lane 后，[downloaded_material_e2e.py](../tests/ci/downloaded_material_e2e.py)
先要求原始完整 gate 通过，再重新生成真实 Linux receipt、归档解包并复验 gate。缺文件、
错误 fixture/input/lifecycle 即使重新计算外层 hash 也必须以指定原因拒绝。原 payload 不被
修改。[transport_e2e.py](../tests/ci/transport_e2e.py) 用固定独立输入检查原 JSON/原始流
字节、独立副本、模式、严格拒绝和实际并发变化；
[receipt_materialization_e2e.py](../tests/ci/receipt_materialization_e2e.py) 检查选中文件链接的
物化顺序、变化拒绝和失败材料保留。传输完整性不能替代原 `control.py`/`evidence.py`
的执行身份、case 集合、原始协议和领域验收。

本地归档比较应固定 Python/zlib、完整逻辑 inventory 和测试选择，交替重复当前 raw receipt
布局的 ZIP6 重放与实际 CAS CLI，分别记录完整 archive 字节及归档、恢复、独立复核、
原验证器重放耗时。ZIP6 布局重放不是 upload-artifact SDK 测量；历史 STORED0 下载包也
不能充当当前 ZIP6 基线。历史 lane 必须使用其原始 source/run/attempt 和匹配的 pinned
验证器，并明确标注没有新执行消费者或完整 matrix。hosted 上传、下载、排队和自然 PR
样本只能来自真实 workflow；本地压缩数据不证明 hosted 或整个 workflow 加速。

各 job 尝试以 `always()` 上传证据，保留期以 YAML 为准，通常 30 天。记录包含原始输出、
命令及 timeout 回执、JSON 断言、协议、输入/源码/binary hash、工具链和实际资源限制。
若 job 在上传前终止，需结合 GitHub job log，不能声称 artifact 必然完整。

## 本地运行入口

使用仓库固定 Go/Rust、Python 和 C 编译器，并为每次执行选择新输出目录。Windows binary
使用 `.exe` 后缀；需要 release 身份时，从干净 commit 按 workflow 传入 release version。
包含固定 Argo/Helm 工具的 extended 和相关 mutation 示例在 Linux amd64 执行；
`install-tools.py` 不支持其他主机，五平台 native suite 按各自 workflow 运行。

```sh
python3 tests/ci/run.py build --output artifacts/ci/build
python3 tests/ci/run.py suite --suite native \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/native
python3 tests/ci/run.py package \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/package
python3 tests/ci/install-tools.py --output artifacts/ci/tools
python3 tests/ci/run.py suite --suite extended --tools artifacts/ci/tools \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/extended
python3 tests/ci/run.py schema \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/schema
python3 tests/ci/run.py mutations --group core --output artifacts/ci/mutations-core
python3 tests/ci/run.py mutations --group audit --output artifacts/ci/mutations-audit
python3 tests/ci/run.py mutations --group contracts --tools artifacts/ci/tools --output artifacts/ci/mutations-contracts
python3 tests/ci/run.py mutations --group legacy --tools artifacts/ci/tools --output artifacts/ci/mutations-legacy
```

Linux 资源入口还要求 systemd、cgroup v2、四 CPU 和非交互 sudo：

```sh
python3 tests/ci/resources.py --suite adversarial \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/adversarial
python3 tests/ci/resources.py --suite load \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/load
python3 tests/ci/resources.py --suite index \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/index
python3 tests/ci/resources.py --suite queries \
  --binary artifacts/ci/build/argo-workflows-ls --output artifacts/ci/queries
python3 tests/ci/run.py mutations --group resources --output artifacts/ci/mutations-resources
```

CI 控制与时间数据入口：

```sh
python3 tests/ci/control.py baseline --output artifacts/baseline.json
python3 tests/ci/control_e2e.py --output artifacts/control-e2e
python3 tests/ci/transport_e2e.py --output artifacts/transport-e2e
python3 tests/ci/receipt_materialization_e2e.py --output artifacts/receipt-materialization-e2e
python3 tests/ci/selection_e2e.py --output artifacts/selection-e2e
python3 tests/e2e/session_e2e.py --output artifacts/session-e2e
python3 tests/ci/trends.py --inputs artifacts/ci --output artifacts/timing.json
```

baseline exporter 读取冻结 Git objects；时间汇总保留首个失败和原命令 hash，不替代产品门槛。
性能对比要求五个 warm 和三个 cold 完整样本，不足时显式报告。Rust/WASI 入口见
[开发指南](../DEVELOPMENT.md)。

单包与 acceptance 集合的传输入口如下；输出目录/文件必须是新的，指标路径留在 gate 树外：

```sh
python3 tests/ci/transport.py pack \
  --receipt artifacts/receipt/native-linux-amd64.json \
  --output artifacts/transport/lane.zip --report artifacts/transport-metrics.json
python3 tests/ci/transport.py hydrate \
  --archive artifacts/transport/lane.zip --output artifacts/one-restored-lane
python3 tests/ci/transport.py hydrate-set \
  --inputs artifacts/receipt-transports --output artifacts/receipts \
  --report artifacts/hydration-metrics.json
```

## 与真实编辑器验收的关系

Native CI 证明实际 CLI/stdio 和包行为；Rust Host 测试证明安装协议；WASI 消费者证明 guest、
原生 probe 和受控文件句柄交互；文件安装测试证明安装与回滚。它们都不等同完整 Zed GUI。

真实编辑器验收记录 Zed/OS、adapter/native 身份、设置和日志，观察首次下载、离线缓存、
诊断/补全/导航、允许与拒绝的 Rename、Quick Fix/undo、配置恢复和 YAML/Helm 共存。
操作清单见 [分发与安装](distribution.md)，已发生问题的根因和回归依据见
[经验教训](lessons-learned.md)。
