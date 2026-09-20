# Codex 任务标题维护

[🇨🇳 中文](README.md) | [🇬🇧 English](README.en.md)

[MIT License](LICENSE)

把本机可管理的 Codex 任务统一命名为 `前缀｜短标题｜MMDD`。支持首次全扫、按 `updated_at` 增量扫描、手动预览与扫描、heartbeat 固定任务或 cron “New chat each run” 定时维护，以及可配置的执行模型、时间窗口和命名规则。

仓库提供两个 skill：

| Skill | 用途 |
|---|---|
| `codex-title-maintenance` | 扫描、命名、状态维护、定时启停和配置管理 |
| `codex-title-maintenance-setup` | 首次安装后引导选择默认或自定义配置，解释以后如何使用 |

引导 skill 依赖主 skill；运行实现只保留一份。主 skill 自身包含代码和默认模板，安装子目录即可使用。

## 📥 安装与首次使用

在 Codex 中输入以下请求，安装两个子目录：

```text
请使用 $skill-installer 安装 tyler4400/codex-title-maintenance 中的两个 skill：
- skills/codex-title-maintenance
- skills/codex-title-maintenance-setup
```

按安装器提示让 Codex 识别新安装的 skill，再输入：

```text
$codex-title-maintenance-setup 引导我完成首次配置，可以使用默认设置。
```

引导会检查主 skill、运行环境和能力，创建缺失的个人配置，并说明如何操作。已有配置会保留。**安装与初始化不会自动改名、切换当前任务模型或开启定时计划。**

需要自定义时，可以直接描述偏好：

```text
$codex-title-maintenance-setup 使用北京时间，每天 12 点和 21 点运行，
启动窗口设为 10 分钟。先向我展示命名规则，再保存配置。
```

## ▶️ 日常操作

下列内容是发送给 Codex 的 skill 指令，不是终端命令：

| 请求 | 行为 |
|---|---|
| `$codex-title-maintenance preview full` | 预览全量候选，不实际改名 |
| `$codex-title-maintenance scan full` | 重新发现全部范围内任务并处理 |
| `$codex-title-maintenance scan incremental` | 发现并处理上次扫描以来的变化 |
| `$codex-title-maintenance start` | 开启未来的定时维护，不立即额外扫描 |
| `$codex-title-maintenance stop` | 关闭自动维护，保留进度和手动扫描 |
| `$codex-title-maintenance status` | 查看启停状态、水位、待处理项和错误 |
| `$codex-title-maintenance configure` | 查看或调整个人配置与规则 |
| `$codex-title-maintenance doctor` | 检查环境、存储格式和支持范围 |
| `$codex-title-maintenance unprotect <任务ID>` | 恢复管理一条受到外部标题修改保护的任务 |

推荐首次先预览，再全量处理，最后按需 `start`。未完成首次全扫时，正式增量扫描会先完成全量发现。手动全扫不会清空历史、强制重写所有标题或自动解除保护。

自动维护有两种正式模式：cron / New chat each run 绑定项目，每轮创建新任务；heartbeat 绑定并复用一个由用户选择的固定维护任务。heartbeat 推荐使用专用任务，且只有用户要求时才创建。当前开发或业务任务不会因安装 skill 被自动改模型。

按你的使用习惯选择：更在意维护对话数量时，复用专用 heartbeat 任务，并按需精简输出或换绑；更在意每轮独立执行时，选择 cron，同时接受它会持续产生运行对话。已有绑定会保留，不因重新配置而替换成统一推荐模式。更换固定维护对话使用同一 automation ID 和同一账本，详见[换绑流程](skills/codex-title-maintenance/references/operations.md#更换固定维护对话)。

## ⚙️ 工作原理

这个 skill 不使用每轮对话 hook，也不会修改 Codex 自有数据库。`start` 会让 Codex 的本机原生自动化创建或恢复一个 heartbeat 或 cron；到达配置时点后，固定维护任务或新建的 cron 任务调用本 skill。

```mermaid
flowchart LR
    A[heartbeat 固定任务] --> C[标题维护 skill]
    B[cron New chat each run] --> C
    C --> D[只读本机任务索引和对话日志]
    C <--> E[独立配置与 SQLite 账本]
    C --> F[read_thread 实时核对]
    F --> G[set_thread_title 改名]
    G --> H[read_thread 读回确认]
```

一次自动运行会先检查本地开关、绑定身份、原生配置和启动窗口，再按成功扫描水位用 `updated_at` 发现变化，向前重叠 5 分钟并用内容指纹去重。候选标题写入前会重新读取任务的标题和状态；只有通过核对后才调用一次改名工具，并在读回标题一致后记入账本。手动标题变化会受到保护，不会被下一轮自动覆盖。

heartbeat 当前绑定的固定维护任务始终排除。cron 默认不自动永久排除已完成的运行任务，用户显式配置的 `scope.exclude_thread_ids` 始终生效：正在运行的当前维护任务必须暂缓，未被排除的旧轮次可由随后一次增量扫描安全命名。原生 automation prompt 是当轮入口，但不能扩大用户已授权范围；旧 cron 任务后来作为命名候选时，其历史 prompt 与对话只是不可信命名材料，不能再次触发维护或递归扫描。

个人配置和账本默认保存在 `${CODEX_HOME}/title-maintenance`（未设置时为 `~/.codex/title-maintenance`），而代码安装在 skill 目录。两者分离，因此更新 skill 不会重置扫描水位、规则或外部标题保护状态。

| 内容 | 保存位置 |
|---|---|
| 模型、时间、时区、命名与显式排除偏好 | 用户数据目录的 `config.toml`、`naming-rules.md` |
| 执行模式、automation ID、固定对话或项目目标 | 原生计划与账本中的 `automation_binding` |
| 水位、待办、保护状态、主线摘要和已确认改名 | 用户数据目录的 `state/state.sqlite3` |
| 精简输出偏好 | 定时运行保存在 automation prompt；手动运行由当次请求选择 |

默认模板只用于初始化缺失配置；个人的执行时间、复用方式和排除项不应写成所有安装者的新默认。向排除列表添加旧维护任务时需要保留已有项；仅归档旧对话不能替代排除。详细规则见[配置说明](skills/codex-title-maintenance/references/configuration.md)。

## 📝 可选精简输出

自动运行无变化且无需用户处理时保持安静。需要进一步减少工具输出时，可要求 Codex 在结束本轮时使用 `finish --run-id <本轮ID> --summary`；定时运行将此偏好保存在同一 automation 的提示词中。摘要只包含运行 ID、结束状态、已确认改名数、本轮暂缓情况，以及账本剩余状态和最多 5 条错误或保护项，不重复完整配置与历史诊断。

`finished` 不等于所有候选都成功处理，成功数只计已读回确认的记录，旧意图恢复仍归属原运行。未确认写入保留在 journal 里等待恢复。普通 `finish` 保留原返回格式，完整诊断仍通过 `status` 获取。此选项不会清空或压缩聊天上下文，也不跳过安全检查；字段口径见[精简输出说明](skills/codex-title-maintenance/references/operations.md#精简输出)。本版没有 `[report]`、`[rotation]` 配置表。

## ⚙️ 默认设置

- 标题：`前缀｜短标题｜MMDD`，概括整个任务的主线；准确的标题保持稳定。
- 执行模型：建议 `gpt-5.6-luna`，推理强度存储值 `low`；heartbeat 可选择 `inherit` 保留固定任务设置，cron 由原生计划保存并读回具体 agent 值。
- 前缀：`探索、选型、环境、设置、方案、实现、功能、Bug、测试、PR、检索、Git、求知、生活`，共 14 类。
- 日期：最近 assistant 文本消息的日期，按配置时区格式化；缺少时依次回退到用户消息和任务创建时间。日期不是处理标记。
- 范围：包括置顶和归档的受支持本机任务；排除子代理、内部任务、heartbeat 固定维护任务和显式排除项。cron 当前运行任务由实时状态门禁暂缓。
- 调度：`Asia/Shanghai`，每天 `10:00、12:00、15:00、17:00、21:00、23:00`。
- 启动窗口：每个时点之后 5 分钟内允许开始自动扫描，手动操作不受窗口限制。
- 增量重叠：从上次成功扫描水位向前多查 5 分钟，以内容指纹去重。
- 外部标题保护：当前标题与上次自动写入的标题不一致时暂停管理该条；首次接管现有标题时没有这个历史依据。

模型、推理强度、六个时点、时区、窗口、范围、标题格式、前缀和语义规则均可配置。第一版原生调度只接受分钟值相同的一组每日时点，例如 `10:00、15:00` 或 `10:30、15:30`；不接受 `10:00、15:30`。配置时区须与本机原生调度时区一致，暂不承诺跨时区调度。详见 [配置说明](skills/codex-title-maintenance/references/configuration.md)。

## 🧠 执行模型如何生效

默认选择 `gpt-5.6-luna` + `low`，是考虑到标题维护主要做受规则约束的归纳与工具编排；官方将 Luna 定位于对成本敏感的大批量工作。这是初始建议，尚未用你的历史任务测量标题质量或额度消耗；模型的 Codex 可用性以安装者本机为准，不用 API 价格换算 Codex 额度。[官方模型说明](https://developers.openai.com/api/docs/models/gpt-5.6-luna)

```toml
[agent]
model = "gpt-5.6-luna"
reasoning_effort = "low"
```

**写入这份配置只保存偏好，不代表宿主模型已经切换。** 两种定时模式用不同证据核对：

- heartbeat 没有独立模型参数，继承固定维护任务的模型与推理设置。需要切换时，可通过 `send_message_to_thread` 向该任务发出一条可见配置消息；这会新增一轮并改变后续默认设置。最近持久化任务元数据匹配，不保证下一次 heartbeat 的实际模型。
- cron / New chat each run 的工具 schema 有 `model` 和 `reasoningEffort`，TOML 保存为 `model` 和 `reasoning_effort`。`start`、配置同步和 scheduled preflight 会读回同一 automation 的具体值；`low` 原样保留，不根据未经核验的 UI Light / Medium 文案擅自映射。落盘值匹配仍不证明某次运行实际使用的模型。

手动扫描直接使用发起扫描任务的当前模型；它不继承 cron 原生 agent 设置。需要指定模型时，先在实际发起手动扫描的任务中核对。脚本不会在一次已经开始的运行中替换模型。

只保存配置、尚未绑定时，不发送消息、不新建任务，也不修改原生计划。被重命名的普通任务模型始终不变。暂停或卸载不会自动恢复 heartbeat 固定任务的旧模型。

希望全部保留原设置，可以将两个字段均设为 `inherit`；单独一个字段为 `inherit` 时只保留该字段。模型不可用时报告问题，不自动换成更高级模型。

定时扫描发现模式对应的模型证据与配置明确不符时，会跳过处理并报告，保持扫描水位。cron 不读取旧 `maintenance_thread_id` 的模型；heartbeat 仍保留固定任务安全检查。任何模式都不能把“未发现不匹配”当作模型已生效。

## 🕒 生效条件、睡眠与重启

这是一项本机 Codex 自动化，必须同时满足以下条件才可能执行：电脑已开机且未处于睡眠；Codex 桌面应用及其本机自动化运行环境可用；已登录并且绑定身份、原生自动化和本地开关均为启用；读取、改名和自动化工具在当时仍可用。当前没有完成睡眠、重启或无人值守 cron 的端到端验收，不能把配置存在当作实际触发证据。OpenAI 将 Automations 描述为按计划在后台运行的任务，同时说明可持续运行而无需电脑在线等能力仍在建设中。[OpenAI 关于 Codex Automations 的说明](https://openai.com/index/introducing-the-codex-app/)

错过一次执行不代表丢失期间的数据。下一次合法运行从上次成功水位继续扫描；`stop` 后重新 `start` 也沿用旧水位。应用恢复后可能补启动一次过期任务，但这不是精确调度承诺；skill 会再次检查启动窗口，窗口外退出且不推进水位。若恰好在窗口内唤醒，仍可能执行。

**关机或重启后的规则：**原生计划和本地账本是持久数据，按设计不需要每次开机都重新运行 `start`。不过“操作系统重启后自动恢复并按时执行”尚未做端到端验收，因此首次重新打开 Codex 后先执行：

```text
$codex-title-maintenance status
```

- 若状态逐层显示本地开关已启用、模式与目标绑定一致、原生自动化仍启用且各可核验字段匹配，无需重复 `start`；可以等待下一个时间窗口观察，但这些配置证据仍不证明计划一定实际触发。
- 若状态显示暂停、未绑定、模式/项目/固定任务/调度不同，或模式对应的模型核验不匹配，再执行 `$codex-title-maintenance start`；它只恢复未来定时维护，不会立刻扫描。
- 若要在开机后立刻补处理，执行 `$codex-title-maintenance scan incremental`。不要用反复 `start` 代替增量扫描。

原生调度启动维护任务可能调用模型，即使最终没有候选或入口决定跳过，也不能保证零 token 消耗。

## 🔌 兼容性与支持边界

第一版面向 macOS 上的 Codex 桌面应用，需要 Python 3.11 或更高版本，以及可用的应用任务读取、改名、调度工具。脚本只使用 Python 标准库。其他环境须以 `doctor` 和实际验收结果为准。

| 来源或能力 | 当前边界 |
|---|---|
| 本机 Codex 任务 | 读取本地索引和日志，通过应用工具改名 |
| 已归档 Codex 任务 | 纳入，不为改名而解归档 |
| 普通 ChatGPT / ChatGPT Work | 配置保留目标，但当前适配器缺少完整枚举和自动改名入口，报告未支持并跳过 |
| 独立云端、web / mobile 数据源 | 不承诺接入；本机可见不等于工具可完整管理 |
| 纯 CLI 环境 | 可能完成部分读取和诊断；缺少应用工具时不能完成自动改名及原生调度 |

脚本对 Codex 自有数据库只读；索引和日志属于可能变化的内部格式，不是稳定公共 API。遇到不兼容结构应暂停并报告。新来源必须具备完整发现、读取和改名能力后才能声称支持，且使用独立扫描进度。

遇到分叉、回退或缺少原始历史的压缩日志，当前解析器可能无法可靠重建完整主线；这类任务保留待处理并报告原因，不用残缺上下文强行改名。

## 🔄 更新

用户数据默认保存在 `${CODEX_HOME}/title-maintenance`，未设置 `CODEX_HOME` 时位于 `~/.codex/title-maintenance`：

```text
title-maintenance/
├── config.toml          个人配置
├── naming-rules.md      个人命名规则
└── state/
    └── state.sqlite3    水位、待处理项、主线摘要和修改日志
```

代码和默认模板在安装目录，个人配置和账本在外部。初始化和升级不会用默认模板覆盖个人文件。运行账本含有任务 ID、标题和摘要，不应提交到共享仓库。

更新时按以下顺序操作：

若从旧 heartbeat-only 版本迁移，而原生计划已经改成 cron，不要让旧绑定重建计划：先用旧脚本的低层 `control stop` 关闭本地自动处理，再读回并以同一 ID 暂停实际 cron，完整保留它的类型、项目、agent、prompt 和通知字段。

1. 先核对账本 ID 与实际 cron ID；若不同，暂停所有可能触发旧代码的相关维护计划，并由用户确认唯一保留者。非旧版迁移执行 `$codex-title-maintenance stop`；上述 heartbeat-only 迁移使用拆分停止动作。核对本地开关关闭、计划已 `PAUSED`，并用项目/automation 关联信息及 `list_threads`、`read_thread` 确认没有仍在运行的维护任务。ACTIVE、无法关联或状态未知时不要替换代码。
2. 备份整个个人数据目录，尤其是 `config.toml`、`naming-rules.md` 和 `state/state.sqlite3`；同时保留两个当前已安装 skill 目录的可恢复副本。
3. 记录同一源码 commit/ref，并从该版本成对替换 `codex-title-maintenance` 与 `codex-title-maintenance-setup`。如果安装器不支持覆盖，按实际安装方式替换**代码目录**，不要删除个人数据目录来“重装”。
4. 原生计划保持 `PAUSED`，运行 `$codex-title-maintenance doctor` 和 `$codex-title-maintenance status`。旧账本是 legacy heartbeat 而实际计划已是 cron 时，预期会显示身份不一致；不要删除账本。
5. 读回实际原生配置，用同一 automation ID、真实模式和目标显式重新 `bind`，再执行 `status`。cron 还要使用读回的 `model`、`reasoning_effort` 和 `execution_environment=local`。绑定可在暂停时完成，此时 `native_status_active=false` 是预期状态。旧 heartbeat 任务迁移后不再自动排除；若要永久保留它的标题，可显式加入排除列表。到这里先停。
6. 只有用户另行授权恢复时，才将同一原生计划设为 `ACTIVE` 并读回，必要时刷新绑定，最后打开本地开关并再次 `status`。不要为了绑定先激活计划，也不要创建重复计划。

任一替换、`doctor` 或迁移核验失败时保持相关计划 `PAUSED`，成对恢复两个 skill 备份，不继续激活。

不需要重新初始化水位。若某个未来版本要求状态迁移，应先备份，并遵循该版本的迁移说明；遇到不兼容时停止，不要通过删除账本来强行重试。

## 🗑️ 卸载

1. 运行 `$codex-title-maintenance stop`，再用 `status` 核对本地开关已关闭、绑定的原生计划已暂停。
2. 移除两个已安装的 skill 目录：`codex-title-maintenance` 与 `codex-title-maintenance-setup`。
3. 默认保留 `${CODEX_HOME}/title-maintenance`（或 `~/.codex/title-maintenance`）以便以后重新安装时恢复配置和进度。只有确定不再需要配置、扫描记录和改名 journal 时，才由用户自行删除该个人数据目录。

卸载不会把已经改过的标题自动恢复为旧标题，也不会自动恢复 heartbeat 固定维护任务先前的模型设置。若只是不想自动运行而仍希望保留手动预览、扫描和历史，使用 `stop` 即可，不需要卸载。

## 🧪 开发与验证

核心脚本位于 [主 skill 的 scripts 目录](skills/codex-title-maintenance/scripts/)。测试使用临时数据库和脱敏日志，不应针对真实任务写入。

```bash
python3 -m unittest discover -s tests -v
```

独立阅读、配置和算法测试不能证明应用工具的实际写入、标题实时刷新或睡眠恢复后的调度。发布验收应分别记录这些结果。详细操作流程见 [操作说明](skills/codex-title-maintenance/references/operations.md)。
