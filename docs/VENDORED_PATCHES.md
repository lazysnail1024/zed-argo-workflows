# Vendored 依赖补丁

当前原生服务有两处明确登记的第三方源码修改。它们不属于共享 toolkit 的领域能力，
也不是未记录的 vendor 漂移。固定模块版本与原始许可证保留不变。

| 依赖 | 修改位置 | 解决的问题与语义边界 |
| --- | --- | --- |
| `github.com/expr-lang/expr v1.16.9` | [file/error.go](../vendor/github.com/expr-lang/expr/file/error.go) | 错误指针缩进使用 `strings.Builder`，避免逐字符拼接的二次分配；不改 tokenizer/parser、坐标或对外错误文字 |
| `github.com/santhosh-tekuri/jsonschema/v6 v6.0.3` | [compiler.go](../vendor/github.com/santhosh-tekuri/jsonschema/v6/compiler.go) | pending schema 以 `map[urlPtr]*Schema` 查找，同时保留 ordered slice 的入队/遍历/最终缓存插入顺序；不新增编译并发状态 |

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

该脚本运行 vendoring，恢复 parser 的原生源码树，并验证、应用两项补丁。
单独执行 `go mod vendor` 不足以恢复本仓可构建输入和补丁。
共享 toolkit 的固定源码身份另由 [toolkit.lock.json](../contracts/toolkit.lock.json)
和 [合同](../contracts/toolkit-design-3.md)管理；不能把本仓第三方补丁当作 toolkit 变更。

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
