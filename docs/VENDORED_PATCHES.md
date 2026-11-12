# 构建期依赖补丁

当前原生服务有两处明确登记的第三方源码修改。它们不属于共享 toolkit 的领域能力，
也不是未记录的 vendor 漂移。固定模块版本与原始许可证保留不变。
仓库不提交第三方实现源码：`vendor/` 仅是从固定模块生成、校验后应用补丁的忽略构建目录。

| 依赖 | 修改位置 | 解决的问题与语义边界 |
| --- | --- | --- |
| `github.com/expr-lang/expr v1.16.9` | [file/error.go 的补丁](../contracts/expr-linear-error.patch) | 错误指针缩进使用 `strings.Builder`，避免逐字符拼接的二次分配；不改 tokenizer/parser、坐标或对外错误文字 |
| `github.com/santhosh-tekuri/jsonschema/v6 v6.0.3` | [compiler.go 的补丁](../contracts/jsonschema-indexed-queue.patch) | pending schema 以 `map[urlPtr]*Schema` 查找，同时保留 ordered slice 的入队/遍历/最终缓存插入顺序；不新增编译并发状态 |

## 身份记录与再生成

[expr-patch.json](../contracts/expr-patch.json) 和
[jsonschema-patch.json](../contracts/jsonschema-patch.json)记录模块版本、原始文件 hash、
patch 路径、修改后 hash 和原因；实际 patch 是
[expr-linear-error.patch](../contracts/expr-linear-error.patch) 与
[jsonschema-indexed-queue.patch](../contracts/jsonschema-indexed-queue.patch)。
升级依赖或更新补丁时，必须重新核对这些输入和结果，不能静默接受不同上游源码。

在仓库根执行：

```sh
python3 scripts/vendor-native.py
```

该脚本先执行 `go mod download` / `go mod verify`，拒绝所有 `replace` 和锁文件漂移，
再生成构建目录、恢复 parser 原生源码并验证和应用两项补丁；不修改全局 module cache。
每项实际 patch 只允许修改登记文件，并验证原始及最终 SHA-256；不接受补丁缺失或漂移。
原始许可证单独保存在 `licenses/modules/`，与下载源码的全部 notice 逐字节比较，
使离线 Release 聚合仍能核对来源；产物还携带两个 patch 文件及合同。
单独执行 `go mod vendor` 或直接 `-mod=readonly` 会绕开补丁，不是支持的发布构建方式。
除登记的两项补丁外不再改写上游源码，包括不需要的 `x/text` 注释和 Unicode 转义转换。
共享 toolkit 的固定源码身份另由 [toolkit.lock.json](../contracts/toolkit.lock.json)
和 [合同](../contracts/toolkit-design-5.md)管理；不能把本仓第三方补丁当作 toolkit 变更。
公共 `yamlvalue` 引入的 `gopkg.in/yaml.v3` 保持原始源码和许可证，未作本地语义补丁；
显式 tag 的拒绝策略由插件传入 `yamlvalue.Options`，不修改 YAML 库实现。

## 修改后的验证

Expr 使用 [wf-expr-error-text](../tests/e2e/wf-expr-error-text.py) 核对 ASCII、tab、multibyte 和
multiline 的精确错误文字/坐标，并通过
[表达式预算](../tests/e2e/wf-bounded-expressions.py)及
[LSP 表达式预算](../tests/e2e/wf-bounded-expression-lsp.py)检查有界行为。

JSON Schema 通过 [assets 测试](../internal/assets/schema_test.go)、
[多版本消费者](../tests/e2e/wf-multi-version.py)和
[有约束的 10,000 文件负载](../tests/e2e/wf-bounded-full.py)验证。
保持产品原有五秒分析 deadline、精确 target、完整计数与 schema 语义；
改善局部 profile 不代替真实 cgroup/CLI/LSP 验收。

复现命令和证据判定见 [CI](CI.md)；问题来源归入 [经验教训](lessons-learned.md)，
不另保留按轮次排列的性能实验报告。
