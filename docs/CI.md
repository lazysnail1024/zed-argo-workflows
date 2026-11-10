# 自动化验收

本页描述当前可执行门禁。某个候选是否通过，应查看该源码 commit 对应的完整运行结果；
文档中的门禁配置本身不等于验收通过。原生、adapter 与真实 Zed GUI 的证据分别记录。

## Workflow 与职责

| 入口 | 执行范围 |
| --- | --- |
| [native.yml](../.github/workflows/native.yml) | 五个平台实际构建/执行 Go 服务；Linux 语义、资源与 mutation；汇总 `Acceptance gate` 并校验完整原生分发 |
| [zed.yml](../.github/workflows/zed.yml) | Linux/macOS/Windows 的 Rust 安装器测试；Linux/Windows 的 WASI 安装运行时；独立 WASM、package 和隔离负向控制；汇总 `Zed acceptance gate` |
| [zed-install.yml](../.github/workflows/zed-install.yml) | 三个操作系统使用已发布 `0.0.1-alpha.1` fixture 做文件系统安装、替换、回滚；该 fixture 不等于当前 adapter 或 native pin |
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
| Mutation：core/audit/resources | 同源通过基线后，验证指定语义、publication/cache 或资源保护移除会触发对应断言 |
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
