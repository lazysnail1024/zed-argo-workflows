# 文档入口

本目录维护当前源码的实现、使用与维护规则，以及已经暴露的问题和防回归要求。
实现事实以对应源码、锁文件与可执行测试为准。

| 需要了解什么 | 入口 |
| --- | --- |
| 安装、启动、最小使用示例 | [项目 README](../README.md) |
| 当前模块、索引生命周期、公共依赖和隔离边界 | [当前实现](architecture.md) |
| 已发生的问题、根因、修复与回归依据 | [经验教训](lessons-learned.md) |
| 原生服务、Zed adapter 的构建与依赖更新 | [开发指南](../DEVELOPMENT.md) |
| 配置、自动兼容与精确版本选择 | [配置](configuration.md) |
| 离线 CRD catalog、版本覆盖与更新方法 | [多版本支持](multi-version-support.md) |
| 原生与 adapter 分发、离线安装、恢复 | [分发与安装](distribution.md) |
| CI 门禁、资源约束、运行命令与证据要求 | [自动化验收](CI.md) |
| Expr、JSON Schema 本地补丁及再生成规则 | [依赖补丁](VENDORED_PATCHES.md) |

[toolkit 合同](../contracts/toolkit-design-4.md)与[实现锁文件](../contracts/toolkit.lock.json)
是依赖校验输入；[原生发布说明](native-release-notes.md)是发布 workflow 的输入。
它们保留原路径，不作为方案历史维护。源码能力与用户安装的 release 可能不同，
判断运行行为时应读取实际二进制的 `version --json`。
