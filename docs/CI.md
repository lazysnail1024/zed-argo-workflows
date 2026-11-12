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
| Native build，五个平台 | 每个平台只产生一份 baseline 原生 binary；vendor 构建、`go vet` 与 analysis/assets/lsp/workspace 单元测试；绑定源码/工具链/平台/二进制 hash |
| Native consumers，每个平台两个 lane | 两个独立 runner 消费同一 producer 的 binary；完整 CLI/stdio 消费者和版本矩阵；lane 0 追加原生打包与 release staging |
| Linux race | 同一组 Go 包的 `-race`，覆盖 catalog 生命周期和共享文件权威 |
| Linux semantic，三个 shard | 现有模板/引用/继承/DAG/编辑/生命周期测试，来源资产，固定且校验 hash 的 Argo/Helm 工具；真实 schema 与合成查询 fixture 分开记录 |
| Linux resources：adversarial | aggregate input、表达式、alias/merge、buffer/snapshot、子进程输出上限、取消与后代回收 |
| Linux resources：load | 10,000 文件/50 MiB；CLI 完整计数、冷启动、活跃诊断、查询、600 次持续编辑、10,000 次 open/edit/close 与内存保留 |
| Linux resources：index | 每个 10,000/50,000 WorkflowTemplate 规模五个新进程，每文件 4 KiB padding；完整首轮/维护轮次、当前文件响应、漏事件收敛与 RSS |
| Mutation：core/audit/resources | 同源通过基线后，验证指定语义、publication/cache 或资源保护移除会触发对应断言 |
| Complete native distribution | 五平台原生身份、源码、校验和、原始 license/provenance 与包库存完整性 |

精确消费者清单由 [tests/ci/run.py](../tests/ci/run.py) 的 `NATIVE`/`EXTENDED` 维护；
资源清单由 [resources.py](../tests/ci/resources.py) 维护，不复制一份易过期的测试计数。
当前 3.5.0 与 3.6.0 都是受支持的精确 target；混合 scope 负载要求完整计数、正确版本、
无实验性标记及实际期望的诊断/退出码。

### 原生分片与产物身份

五个平台的 build producer 完成后，每个平台启动两个独立的 consumer runner。
[native_shards.py](../tests/ci/native_shards.py) 按真实套件耗时维持 17/18 的固定清单，
lane 0 同时承担 package/release E2E；每个 runner 内消费者仍串行，原有两秒前台响应、
五秒分析和资源门槛不变。当前 matrix 的 `needs` 会等待五个平台 producer 都完成，
这项 barrier、额外 runner 启动/产物传输和总 runner-minutes 都要计入实际提速评估。

传输 tar 仅接受普通文件和目录，接收端独立校验归档、原始 build manifest、source SHA、
实际原生平台、二进制 SHA256，并执行 `version --json` 核对身份；下载动作本身的校验告警不能代替硬失败。
producer 的原始绝对路径 manifest 不修改，接收端单独生成带原 manifest hash 的路径重定位版本用于 staging。
两个 lane 仍安装 Go/C 工具链，慢 I/O 与诊断 deadline 的 overlay 构建不能用 baseline 替代。

`Acceptance gate` 除要求所有任务成功，还从当前源码重算五个平台、十份 lane 收据、全部 35 个消费者、
146 个精确版本和 1,052 个版本/资源对，拒绝缺片、重复、错源码/平台/版本/producer/binary、
未完成命令或顶层 success 掩盖的语义失败。独立 producer 收据与两份 lane 的 binary hash 必须一致；
完整原生分发仍额外校验五份平台包。小型收据补充原始证据，不替代同一次 workflow 的任务状态和可信源码。

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
`request_consistency_test.go` 确定性复现关闭文件的旧读票被新读票替代，重试在字节不变时仍撤销旧 stamp 证明。
audit mutation group 同时移除正确的输出上限、注入分页漏项，要求上述真实消费者报告指定语义失败，编译失败或超时不能算通过。
这些测试不替代资源 suite 现有的 10,000 文件/20,000 符号五次冷索引验收。

`internal/lsp/catalog_test.go` 的正向用例覆盖共享 Documents、队列及完整扫描的接入。
额外的 [ablate-shared-lifecycle.py](../tests/e2e/ablate-shared-lifecycle.py) 是可重复的七项控制入口；
它并未被当前 native workflow 自动调用，不能写成已有 CI gate。

## 真实资源约束

[resources.py](../tests/ci/resources.py) 使用 systemd transient service，并在 service 内读取和验证
`memory.max`、`memory.swap.max`、`cpu.max` 与四 CPU affinity。构建在测量组外完成；
没有权限、cgroup v2 或足够 CPU 时，资源验收失败，不降级成无约束测试。

| 负载 | 环境与断言 |
| --- | --- |
| adversarial | 通常 512 MiB/no swap；合法精确 128 MiB snapshot case 保留独立 1 GiB 环境 |
| 固定 load | 四 CPU、最多 8 GiB、no swap，消费者保持现有语义/延时/计数要求 |
| 持续 index | 四 CPU、8 GiB/no swap；10k/50k 的每轮收敛分别 60/300 秒，server RSS ≤ 512 MiB |

内存环境、逻辑输入限额、Go 软目标和原生进程 RSS 是不同指标。
测试同时保存它们；受约束的固定 fixture 通过不能外推成任意磁盘/编辑器的性能保证。
Linux FIFO、inotify、`/proc` 和进程组控制不代表 Windows/macOS 对应系统机制已被验证。

## 结果判定和防虚假通过

进程退出成功和语义证据必须同时满足。报告非空、预期 case 集合完整、`pass`/`passed` 和
所有 assertion 都是布尔 `true`；字符串、缺报告、空选择、prepare-only、异常与超时都失败。
family 测试验证子 worker 的结果，不能仅相信父进程记录。LSP 消费者有绝对响应 deadline，
取消要返回一次 `-32800`，无关消息不能延长等待。

[ablate-ci.py](../tests/e2e/ablate-ci.py) 使用同一个隔离源码构建基线和 mutant；
源选择器必须唯一匹配，mutant 必须成功编译，case/assertion 集合相同且指定语义断言改变。
编译错误、崩溃、timeout、空报告、缺测试或无关失败不算 mutation 检测。
未被当前入口选择的 `ablate-*` 脚本不自动获得 CI gate 身份。

报告冻结、非 Workflow 归属、维护 publication、精确编辑字节和候选 generation 等教训，
以及各自的回归测试，统一见 [经验教训](lessons-learned.md)。

## 打包身份与验收留证

release-mode build 从精确 commit 的临时 worktree 构建，防止本地 ignored Go/C/embed 文件混入。
二进制 Go VCS metadata、源码 commit、catalog hash、构建模式和原始 dependency/schema/runtime
法律文本都必须一致。包消费测试实际解压并运行 native identity，独立检查文件库存、hash、权限和拒绝路径。
这是来源校验，不是声明 Go 二进制可逐字节复现。

各 job 以 `always()` 将 `artifacts/ci/` 等完整证据预归档，通常保留 30 天：原始 stdout/stderr、
命令/timeout 回执、JSON 断言、协议流量、源码/二进制 hash、工具链、实际资源限制和产物。
`tests/ci/evidence.py` 仅接受 checkout 的 `artifacts/` 下生成目录，使用 gzip level 1 打包，
再流式核验所有成员及内嵌 JSONL inventory；上传层设 `compression-level: 0`，避免对大量小文件
逐个处理和重复压缩。`evidence.json` 保留归档 SHA256、inventory SHA256、文件字节数及打包/核验耗时。
原始 fixture 不删除，隐藏的 synthetic Git history、执行权限、符号链接目标和 FIFO 类型均保留；
链接不解引用，FIFO 不读取。证据归档不等同于可安全直接解压执行：核验器不会解压，重放前需审查链接/特殊节点并按原测试命令重建。
归档校验失败会使 job 失败，不上传未验证的归档；job 在上传前被终止时不能承诺完整 artifact。

普通编译缓存使用 `setup-go`，key 的输入还包含 C 编译器版本、runner image版本、Go依赖锁及vendored模块清单；
额外指纹覆盖 vendored C、header 和 `.inc` 字节，避免嵌套 include 的修补在不改锁文件时复用旧原生对象。
缓存只复用编译中间态，不缓存测试通过状态；`-count=1`、race、真实消费者、故障注入和干净源码边界不变。
Python固定在 `3.14` minor，允许复用runner已安装的兼容patch，实际版本记录仍保留在构建证据中。

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
