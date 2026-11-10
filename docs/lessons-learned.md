# 经验教训

这里只保留已经发生的问题、被验证的根因和当前防回归方法。
“用户故障”来自实际启动/编辑反馈，“CI 故障”来自真实构建或消费测试，
“审计/资源实验”来自有对照的复现；后者不写成已发生的线上事故。
实现分工见 [architecture](architecture.md)，运行命令与证据规则见 [CI](CI.md)。

## 1. Windows 验证成功后仍无法激活原生服务

**用户故障。** 用户先看到 `Cannot activate verified server: Permission denied (os error 2)`，
随后临时目录清理也失败。旧安装器在下载、hash 和实际运行 EXE 后才重命名整个目录；
Windows 不允许删除共享的句柄可以同时阻止移动和清理。确认的是此设计弱点，
原机器上哪个进程持有句柄没有被观察到；WASI 返回的数字不能直接当作 Win32 错误码。

**当前修复。** 预留最终 `.gen2-*` 路径，在该路径完成验证，最后原子创建 `.complete/`。
不移动已执行的 EXE，不替换其他进程使用的 generation；未完成的新目录不能被离线安装器误认为缓存。
每次复用仍检查类型、大小、hash 和原生身份；旧缓存布局独立验证。

**防回归。** 原 Rust `FakeHost` 不执行下载的 EXE，因而 Windows runner 通过也不能覆盖真实句柄行为。
现在同时检查原生 Rust 协议测试与 WASI 运行时：固定路径、未完成目录、并发安装、旧缓存和
受控 Windows 禁止删除句柄；后者仍不代表真实 Zed GUI 或原用户的句柄来源。
[修复 PR #9](https://github.com/lazysnail1024/zed-argo-workflows/pull/9)、
[安装器](../editors/zed/src/distribution.rs)、
[运行时消费测试](../editors/zed/tests/cache_runtime_e2e.py)、
[负向对照](../editors/zed/tests/ablate_distribution.py)

## 2. 普通 YAML 被报成 Workflow 分析超时

**用户故障。** adapter 挂到 YAML/Helm buffer 后，服务曾先获取 workspace、执行外部引用分析，
再判定文件是否属于 Workflows。配置错误会污染 Deployment/Application/values 文件；
一个慢 Workflow 耗尽共享发布 deadline 后，未访问的无关文件也收到 `WF_ANALYSIS_INCOMPLETE`。

**当前修复。** 先依据顶层 API group/kind 判断归属，并先清空外部 YAML 的诊断。
八种正向 GVK、quoted/flow identity、有界 alias/merge 和混合文档流都参与识别；
无法从损坏 YAML 确定归属时记日志，通用 YAML 服务处理语法诊断。真实 Workflow 超时和显式 buffer 拒绝仍可见。

**防回归。** 用无效配置、六 root 慢分析控制、八种资源和未保存的归属变化检查实际 stdio publication，
包括 Workflow 改成普通 YAML 后清空旧诊断；移除 ownership 检查必须触发指定失败。
[PR #11](https://github.com/lazysnail1024/zed-argo-workflows/pull/11)、
[publication.go](../internal/lsp/publication.go)、
[归属测试](../tests/e2e/wf-diagnostic-ownership.py)、
[deadline 测试](../tests/e2e/wf-diagnostic-deadline.py)

## 3. 大目录发现绑定到一次请求的 deadline

**重构与慢 I/O 审计。** 原来编辑器按请求获取 workspace，持续输入会取消旧工作；
若把目录发现绑定到五秒 context，过期 context 和重复全目录读取会阻止索引持续收敛。
冷 workspace-symbols 的串行解析也曾在 CI 负载中超时。

**当前修复。** catalog 使用会话 context；单文件工作、前台请求和诊断 worker 各有自己的 deadline。
当前 buffer 本地检查和相关目录优先，前台只物化所需依赖；workspace symbols 使用摘要。
CLI 保留一次性收集，通过最多四个 parser、每批 64 KiB 的有界预解析改善吞吐。

**防回归。** `wf-persistent-slowio` 在真实单文件读边界加 15 ms，保留生产 deadline 并持续编辑，
要求 discovery 超过五秒后仍能 Ready；dense-resource 测试检查当前文件响应、完整收敛和进程 RSS。
[冷扫描问题 PR #4](https://github.com/lazysnail1024/zed-argo-workflows/pull/4)、
[catalog](../internal/lsp/catalog.go)、[预解析](../internal/analysis/preparse.go)、
[慢 I/O 控制](../tests/e2e/wf-persistent-slowio.py)、[持续索引](../tests/e2e/wf-persistent-workspace.py)

## 4. 生命周期边界不能由重复状态“碰巧同步”

**重构审计反例。** 去掉 root/epoch 检查会让旧扫描删除新 root 的文件；
漏记 close observation 会把扫描后创建并保存的文件清掉；取消时 Done 不代表完整扫描。
root 移除也不能关闭仍由编辑器持有的文本。拒绝输入若不推进 stamp，尚未取得 index ticket 的旧 worker 仍可能交付。
这些是代码控制实证，不是另报一组用户事故。

**当前修复。** Documents 作为文本权威，WorkQueue 管理 ready/overflow/running，Membership 管理扫描观察。
输入接受与失效在同一 catalog 锁内完成；只有当前 root/epoch 的完整扫描能 sweep。
资源条目满后保留协议身份并报告 unavailable，释放槽位后的完整替换可恢复，服务不退出。

**防回归。** [catalog_test.go](../internal/lsp/catalog_test.go) 覆盖 UTF-16 顺序更新、非法批次原子性、
过时 version、配置 1 MiB、拒绝恢复、root 移除、obsolete sweep、close observation 和取消扫描。
[七个独立负向控制](../tests/e2e/ablate-shared-lifecycle.py) 先要求相同 Go 测试基线通过，
每个 mutant 单独编译并命中指定断言；编译失败、panic、超时和找不到测试不能算检测成功。
[共享接入 PR #13](https://github.com/lazysnail1024/zed-argo-workflows/pull/13)

## 5. 查询返回正确，不代表外部诊断已发布

**审计中复现。** 前台查询曾推进外部诊断完成标志，使漏 watcher 的创建/删除在查询后不再触发应有诊断。
其他反例包括 scope 改名后保留旧依赖、候选资源新增却复用旧 completion、配置错误走错诊断路径，
以及 excluded buffer 的 Quick Fix resolve 重新获取了错误 scope。

**当前修复。** 只有被接受的 publication 确认诊断工作；query cache 在取得当前输入后使用，
并包含脱离源码闭包的候选 generation。配置错误有独立诊断，`opened:<URI>` 重新按当前 buffer 归属取得输入。
维护轮次恢复漏 watcher，同时无变化的维护不废弃仍有效的 Quick Fix。

**防回归。** 同时观察 query/no-query 客户端和真实维护轮次，检查无 watcher 的创建与删除、
空闲 token、配置修复和最终应用字节；I/O 测试确认本地查询零资源读、外部查询不读无关 scope。
[PR #2](https://github.com/lazysnail1024/zed-argo-workflows/pull/2)、
[诊断确认](../internal/lsp/catalog_inputs.go)、[query cache](../internal/lsp/query_cache.go)、
[维护测试](../tests/e2e/wf-reaudit-reconciliation.py)、[动作测试](../tests/e2e/wf-reaudit-actions.py)、
[I/O 测试](../tests/e2e/wf-reaudit-query-io.py)

## 6. 文本相似不能证明绑定和可编辑位置

**语义审计反例。** metadata 的字面量曾被当作 Argo 引用，导致 Rename 修改普通 annotation，
或普通 prose 意外阻止 Rename。alias/merge、继承和动态值还可能具有语义依赖，但没有唯一可编辑位置。

**当前修复。** 域分析区分字段角色、标记表达式、原始引用和字面量；记录 exact/derived/shared 来源。
共享索引仅接受插件提供的 key/span，插件保留无精确位置的依赖，安全编辑重新证明作用域与绑定。
缺失 target 也必须保留依赖，才能在其后来出现时更新调用方。

**防回归。** 检查最终替换字节、未修改的 metadata、歧义拒绝和缺失资源创建后的失效，不能只检查“有返回值”。
[PR #2](https://github.com/lazysnail1024/zed-argo-workflows/pull/2)、
[Rename 审计](../tests/e2e/wf-reaudit-rename.py)、[语义来源](../internal/analysis/semantic_origin.go)、
[摘要位置测试](../internal/analysis/index_test.go)

## 7. 逻辑缓存预算不能代表进程内存

**资源实验。** 关闭源码 LRU 在先读文件后查缓存，保留整份文本却没有省掉 I/O；
大目录试验触发既有 RSS 门槛。另一个真实 stdio 控制把 open text 填满后，
依赖闭包只计算自身文本，仍允许获取新关闭依赖。纠正预算后，并发冷 schema 编译和已结束 worker 的引用保留又暴露额外内存峰值。

**当前修复。** 移除 catalog 关闭文本 LRU，摘要复制所需短字符串；请求先预留所有 open buffers，
再为关闭依赖收费。冷编译一个许可，暖命中直达；完成 worker 释放 context/maps，编辑计划复制字符串。
LSP 使用 288 MiB Go 软目标并保留更低外部限制，不把此值描述为硬 RSS 上限。

**防回归。** 保持精确 128 MiB 输入、溢出拒绝、释放后恢复、摘要查询与真实进程 RSS 断言；
不要用删小 transport channel 或仅看缓存计数替代根因验证。
[快照预算测试](../tests/e2e/wf-bounded-snapshot-budget.py)、
[分配前预算](../tests/e2e/wf-preflight-budget.py)、
[冷编译许可测试](../internal/assets/schema_test.go)、[内存策略测试](../internal/lsp/memory_test.go)、
[大目录测试](../tests/e2e/wf-persistent-workspace.py)

## 8. 第三方错误格式化和编译队列也会成为热点

**性能审计/负载故障。** Expr 的错误指针缩进逐字符拼接产生二次分配；
JSON Schema 编译每次入队线性扫描 pending graph，增加冷启动成本并影响既有 CLI deadline。
它们发生在实际依赖路径，不能通过增加 timeout 掩盖。

**当前修复。** Expr 只将缩进构造替换为 Builder；JSON Schema 用 `urlPtr` map 查找并保留有序队列。
模块版本、词法/解析语义和错误坐标不变，原始输入、patch 和结果 hash 明确记录。

**防回归。** 再生成 vendor 必须走验证脚本；Expr 检查 ASCII/tab/multibyte/multiline 的精确错误文本，
schema 检查冷/暖加载、版本语义和原有 10,000 文件 deadline。
[补丁操作规则](VENDORED_PATCHES.md)、[Expr 精确文本测试](../tests/e2e/wf-expr-error-text.py)、
[表达式预算](../tests/e2e/wf-bounded-expressions.py)、[原负载消费者](../tests/e2e/wf-bounded-full.py)

## 9. 报告已完整，stdout 阻塞却改变退出码

**真实 CI 故障。** 分析在五秒内完成，较大的报告写入 stdout 越过 deadline；
写完后再次读取 `ctx.Err()`，把已发布 `analysisComplete: true` 的结果改成退出码 3。

**当前修复。** 分析结束时冻结终止状态，报告、target 完整性和退出码使用同一结果；
输出反压不会把完成的分析追溯改成超时，真正未完成的分析仍报告不完整并退出 3。

**防回归。** 真实消费进程读取首字节后暂停超过五秒，再排空 stdout；完整 JSON 成功/错误和 human
输出分别应返回 0/1/0。恢复旧检查的 mutation 必须命中三个 `exitMatchesPublishedOutcome` 断言。
[PR #5](https://github.com/lazysnail1024/zed-argo-workflows/pull/5)、
[报告入口](../cmd/argo-workflows-ls/main.go)、[反压测试](../tests/e2e/wf-report-deadline.py)、
[真实超时/计数测试](../tests/e2e/wf-bounded-external-counts.py)

## 10. 多版本总耗时与单版本产品 deadline 要分开

**真实 CI 故障。** macOS Intel 的多版本消费者跑到 300 秒时只完成 121/146 个版本。
已完成的分析语义仍正确，其他套件也变慢；同一代码树的对比支持 runner 性能波动，
不能据此认定版本语义回归，也不能接受残缺报告。

**当前修复。** 将 146 个版本分成 37/37/36/36 四个串行 shard，每条命令仍 300 秒，
单版本子进程 watchdog 仍 20 秒，产品分析仍五秒。最终按锁定目录核对 146 版本、1,052 个
版本/资源对，无重复遗漏；最后 shard 保留其他 CLI/LSP 语义断言。
首轮合成自测还暴露 macOS `/var` 与 `/private/var` 路径别名，证据根统一 canonicalize。

**防回归。** 12 个聚合验证器自测拒绝缺 shard、遗漏/重复版本、字符串 true、伪造 CLI 报告、
缺少原始输出、指标越界和身份不匹配；每个原生平台仍运行真实版本消费者。
[PR #10](https://github.com/lazysnail1024/zed-argo-workflows/pull/10)、
[聚合验证](../tests/ci/run.py)、[自测](../tests/ci/test_multi_version.py)、
[版本消费者](../tests/e2e/wf-multi-version.py)

## 11. 测试本身会产生虚假通过或虚假失败

**CI 与消融控制中观察。** 仅凭子进程退出码、空结果或 truthy 字符串会误判通过；
编译失败/超时不能证明 mutation 被正确检测。另一次 schema 展开控制只有一个有效输入超时，
旧判定却要求两个都超时，造成“未检测到回归”的虚假失败。

**当前规则。** 正常门禁同时要求进程成功、完整非空 case 集合和精确布尔断言。
mutation 要求通过基线、成功构建、相同 case/assertion inventory 和预期语义断言失败。
schema 资源控制接受已完成有效样本与明确分析 deadline 样本的合法组合，同时保留大小和 RSS 比例门槛；
其他错误码、异常诊断和不相关 incomplete 都不能代替证据。

**防回归。** 同时检查正向与反向分类器；保存源码/二进制 hash、原始输出和命令回执。
[CI 加固 PR #1](https://github.com/lazysnail1024/zed-argo-workflows/pull/1)、
[mutation 门禁](../tests/e2e/ablate-ci.py)、[版本消融及 self-test](../tests/e2e/wf-multi-version-ablation.py)、
[当前证据验证](../tests/ci/run.py)

## 12. 跨平台相同源码还需要相同身份和输入字节

**CI 故障。** Windows 盘符错误进入 file URI authority；夹具换行被宿主转换，
字节 hash/源码范围不一致。大小写同名文档也曾令 macOS/Windows checkout 变脏；
用短时间“没有消息”推断语义正确则在慢 runner 上不稳定。

**当前规则。** 所有本地 URI 使用统一转换函数；夹具显式选择 UTF-8 和 LF/CRLF；
交付要求干净源码及 commit/catalog 身份一致。路径不得依赖仅大小写区别，
时序测试等待对应输入版本与内容的结果，不能拿旧空 publication 或短暂静默充当通过。

**防回归。** 五个平台实际运行 CLI/stdio 与打包验证，检查版本/范围/应用字节和 exact source identity。
[PR #1](https://github.com/lazysnail1024/zed-argo-workflows/pull/1)、
[PR #7](https://github.com/lazysnail1024/zed-argo-workflows/pull/7)、
[file URI](../internal/fileuri/fileuri.go)、[字节规则](../.gitattributes)、
[CLI/LSP 生命周期](../tests/e2e/wf-remediation-lifecycle.py)、[包验证](../tests/e2e/native-package.py)

这些规则描述当前源码要求；某次修复是否已经交付，应核对实际 release 和二进制身份。
CI、WASI 运行时与真实 Zed 编辑会话的结果分别记录，不能互相代替。
