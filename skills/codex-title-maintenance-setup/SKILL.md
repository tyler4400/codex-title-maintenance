---
name: codex-title-maintenance-setup
description: 引导首次安装 Codex 任务标题维护 skill 的用户检查环境、选择默认或自定义配置，并说明预览、扫描、start 和 stop 的使用方法。也用于重新配置；扫描和改名交给 codex-title-maintenance。
---

# 任务标题维护：首次引导

用简短的交互把用户带到“知道配置、知道支持范围、知道以后如何使用”的状态。执行逻辑只存在于主 skill，本 skill 不复制扫描脚本或命名规则。

## 1. 检查主 skill

定位已安装的 `codex-title-maintenance/SKILL.md` 并读取。找不到时，说明需要同仓库的主 skill；使用 `$skill-installer` 安装 `tyler4400/codex-title-maintenance` 中的 `skills/codex-title-maintenance`，仅在用户的安装授权范围内进行。安装不了则提供准确路径，不自行实现另一套逻辑。

说明主 skill 的职责与当前兼容范围。按其 `doctor` 流程核验本机；不要因为用户勾选了 Chat / Work 就宣称支持，也不要把静态工具名称当作端到端通过。

首次尚未 `init` 时，`doctor` 可能报告配置不存在，这是尚未初始化的状态；完成初始化后再复检。若同时存在源索引或工具能力问题，应分别报告。

## 2. 默认或自定义

先读取已有配置。已有设置完整时，说明会保留，询问其想调整的部分；不能重置成默认。

新用户展示实际模板中的关键默认值，不凭记忆生成另一份配置：

- 默认标题 `前缀｜短标题｜MMDD`，按整个任务主线命名。
- 推荐执行模型 `gpt-5.6-luna`、推理强度存储值 `low`；可选本机支持的其他模型。heartbeat 可用 `inherit` 保留固定维护任务现有设置；cron 创建时必须按原生 schema 选择具体 model / reasoning，不能猜测 UI 档位文案的映射。
- 默认 14 个前缀，其中允许 `功能`；用户可替换前缀及命名规则。
- 日期默认取最近 assistant 消息时间，不充当处理标记。
- 默认纳入归档及实际支持的本机来源，排除内部子任务。heartbeat 固定维护任务始终排除；cron 当前运行任务通过实时状态暂缓，已结束的旧轮次可由后续增量处理。
- 默认北京时间每天 10、12、15、17、21、23 点，5 分钟启动窗口；窗口内唤醒可能执行，窗口外跳过，之后由增量水位补齐未处理数据。
- 安装和初始化不自动开启计划、不立即改名，也不切换当前任务模型。

如果用户已经明确“使用默认配置”，直接继续初始化，不能再问同一问题。否则用一个简短问题让用户选择：

```text
**❓Q1. 首次配置采用哪种方式？**

a) 使用默认配置，以后随时调整。

b) 自定义模型、推理强度、时间、时区、启动窗口、范围或命名规则。

👍 a) — 可以先预览标题效果，再调整具体偏好。
```

自定义时只补问用户未给出的必要设置。无需问完所有配置字段；余项保留默认。选定配置方式后，先调用主 skill 的 `init` 创建缺失文件，再用 `config-apply` 应用自定义补丁；已有文件不会被重置。按主 skill 的配置校验与保存流程执行，遇到冲突指出具体字段。

用户要求 `start` 时还要确认原生执行模式；仅初始化或改配置时不必创建计划：

```text
**❓Q2. 定时维护采用哪种运行方式？**

a) cron / New chat each run：绑定项目，每轮新建任务。

b) heartbeat：绑定一个固定维护任务并复用其上下文。

👍 a) — 不会把后续运行长期堆叠到同一维护对话，且原生 model / reasoning 可单独核对。
```

选择 cron 时通过 `list_projects` 或当前工具的实际读回取得项目 ID，不能从名称、cwd 或路径猜测；还需使用 `executionEnvironment=local` 和原生 automation 支持的具体 model / reasoning。选择 heartbeat 时才要求用户选定固定维护任务。执行细节交给主 skill 的 [operations.md](../codex-title-maintenance/references/operations.md)。

## 3. 完成初始化并说明下一步

调用主 skill 的 `init`，创建缺失的个人配置与账本，不覆盖已有数据。让用户知道实际数据目录、兼容性结果，以及当前自动维护仍处于什么状态。

解释维护入口：cron 绑定项目并使用 New chat each run；heartbeat 才复用用户选择的 Codex 任务及其模型设置。heartbeat 推荐专用维护任务，当前开发或业务任务不能自动接管；只有用户明确要求新任务才创建。不要自行创建侧栏任务。

模型配置保存后仍只是偏好。heartbeat 没有独立模型参数；用户执行 `start` 或明确为固定维护任务应用模型设置时，按主 skill 核验，必要时发出一条可见配置消息。cron 则把具体 model / reasoning 写入并读回原生 automation，`low` 原样保留；落盘值匹配仍不能被描述为某次运行已经使用该模型。仅初始化时不发消息、不修改任务模型或原生计划。

用户明确授权了预览、正式扫描或 `start` 时，继续主 skill 对应流程，不把引导变成额外审批。用户只要求安装或配置时，交付以下简明使用说明，不自动改名或创建计划：

```text
$codex-title-maintenance preview full       # 首次查看候选标题
$codex-title-maintenance scan full          # 全量发现并处理
$codex-title-maintenance scan incremental   # 手动补处理新增或更新内容
$codex-title-maintenance start              # 开启未来定时维护，不立即扫描
$codex-title-maintenance stop               # 关闭自动维护，保留状态和手动扫描
$codex-title-maintenance status             # 查看启停、进度和待处理问题
$codex-title-maintenance configure          # 调整个人配置与规则
$codex-title-maintenance doctor             # 重新检查兼容性
```

这些是发送给 Codex 的 skill 指令，不是可直接在终端执行的 shell 命令。首次尚未全扫时，正式增量扫描先完成全量发现。

最后简明交付：采用的配置、已做的初始化、支持与不支持的来源、启停状态、一个适合当前用户的下一步。不要复述整份技术文档。
