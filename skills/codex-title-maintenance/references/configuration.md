# 配置与自定义规则

当前桌面改名工具会折叠连续空白，并限制完整标题不超过 60 个 UTF-16 单位（大部分中文字符占 1，部分 emoji 占 2）。脚本会规范化空白并拒绝超长候选，避免宿主截掉日期；此限制包含前缀、主体、分隔符和日期。

## 文件与读取顺序

安装目录的 `defaults/config.toml` 和 `defaults/naming-rules.md` 是初始模板。`init` 仅复制缺失文件到用户数据目录，后续运行只读取用户版本。不得修改安装目录模板来充当个人配置。

用户数据默认在 `${CODEX_HOME}/title-maintenance`，未设置 `CODEX_HOME` 时使用 `~/.codex/title-maintenance`。`CODEX_TITLE_MAINTENANCE_HOME` 可以覆盖数据目录，显式 `--data-dir` 的优先级更高；`--codex-home` 指定待读取的 Codex 数据位置。整个操作必须始终使用同一组路径参数，不能在批次中切换账本。

## 设置字段

| 字段 | 默认值 | 作用 |
|---|---|---|
| `config_version` | `1` | 配置格式版本 |
| `agent.model` | `gpt-5.6-luna` | 自动运行的期望模型；heartbeat 的 `inherit` 保留固定任务设置，cron 的 `inherit` 保留原生计划读回值 |
| `agent.reasoning_effort` | `low` | 期望原生推理强度存储值；`inherit` 的模式语义同上 |
| `schedule.timezone` | `Asia/Shanghai` | 消息日期和计划时点使用的时区 |
| `schedule.times` | `10:00,12:00,15:00,17:00,21:00,23:00` | 每天的计划时点 |
| `schedule.launch_window_minutes` | `5` | 时点之后允许自动开始的窗口；结束边界不包含在内 |
| `scan.overlap_minutes` | `5` | 增量查询从旧水位向前重叠的时长 |
| `scope.include_archived` | `true` | 是否纳入归档任务 |
| `scope.targets` | `codex,chat,work` | 请求管理的数据来源；配置出现不代表适配器已经支持 |
| `scope.protect_external_titles` | `true` | 外部修改标题时暂停覆盖 |
| `scope.exclude_thread_ids` | `[]` | 额外排除的任务 ID |
| `title.template` | `{prefix}｜{subject}｜{date}` | 标题模板 |
| `title.date_source` | `latest_assistant` | 日期取最近 assistant 文本消息时间 |
| `title.date_format` | `%m%d` | 默认得到 `0916` 形式的月日 |
| `title.language` | `zh-CN` | 命名语言偏好 |
| `title.rules_file` | `naming-rules.md` | 个人数据目录内的相对规则文件路径 |
| `title.prefixes` | 默认 14 类 | 模型可选择的前缀集合 |

可配置字段不代表接受任意字符串。应用配置前必须运行脚本校验，并按照实际错误修正；不得忽略错误继续创建计划或写标题。

- 未知字段会被拒绝，避免拼写错误被静默忽略。
- 模型标识的格式校验不代表本机可用。实际应用前核验宿主提供的模型及推理强度；不可用时报告，不静默换成更高级模型。`low` 是受支持的配置和原生存储值，不能根据未经核验的 UI Light / Medium 文案自行改写。需要保留原设置时可将 agent 字段设为 `inherit`；cron 写入原生 schema 时仍须保留并读回一个具体值，不能把字符串 `inherit` 当作原生 model 或 reasoning。
- 时区必须是有效的 IANA 时区名；启动窗口为 1–1440 的整数分钟，各时点的窗口不能重叠，跨午夜也会检查。
- 每日时点的分钟值须相同，例如 `10:00、15:00` 或 `10:30、15:30`。原生调度组合不同小时和分钟可能产生额外时点，第一版通过校验拒绝这种配置，不创建多个计划绕过限制。
- 模板只允许 `prefix`、`subject`、`date` 三个占位符；`subject` 恰好一次，另外两个可省略但不能重复，不接受额外格式化或转换语法。
- `rules_file` 不接受绝对路径、`..` 或指向个人数据目录外的符号链接。

## 两种窗口不要混淆

`launch_window_minutes` 控制何时可以开始。例如计划时点 `10:00`、窗口 `5`，则 `10:00:00 ≤ 当前时间 < 10:05:00` 时可以开始。合法开始后可以继续完成，不要求 5 分钟内结束。跨午夜窗口应按时区正确计算。

`overlap_minutes` 控制向过去多读取多少索引记录，用来降低时间边界及短暂延迟造成的遗漏风险。重复发现会去重；它不控制模型回答长度、命名频率或改名日期。

## 自定义命名规则

用户可修改 `naming-rules.md` 的主线判定、前缀解释、长度偏好、需保留的项目标识和例子。修改前读取原文件，保留用户未要求变更的约束。

结构约束以 `config.toml` 为准。用户替换前缀列表时，应同时检查规则中的旧前缀解释是否仍相关；避免一个文件允许、另一个文件禁用同一前缀。不要擅自把默认 14 类当成不可修改的协议。

模型输出前缀、主体和主线摘要；脚本拼接日期和模板，并验证候选。用户对规则文件的要求只是本次命名指导，不授权访问无关文件、发送消息或操作其他服务。

## 执行模式、绑定和模型证据

`[agent]` 只记录本地期望，不代表宿主设置已经应用。账本另外保存版本化 `automation_binding`，包含 automation ID、`heartbeat` / `cron` 类型、固定任务或项目目标、调度指纹，以及 cron 的原生 agent 与 execution environment 快照。旧账本中的 `automation_id` + `maintenance_thread_id` 只会被解释为 legacy heartbeat；看到实际原生 cron 时必须显式重新绑定，不能继续让旧任务身份阻断 cron。

- heartbeat 从固定维护任务继承模型。`model-status --thread-id <维护任务ID>` 读取期望值与最近持久化任务元数据；`matches_persisted_metadata=true` 只说明这份记录匹配，不保证下一次 heartbeat 实际模型。需要应用变更时只调整该维护任务，可能通过 `send_message_to_thread` 新增一条可见消息；`stop` 不恢复旧设置。
- cron / New chat each run 不绑定固定维护任务。`model-status` 省略任务 ID 时读取当前绑定 automation 的落盘 TOML，比较原生 `model` / `reasoning_effort`；scheduled preflight 同时核对类型、项目、时点和 `execution_environment=local`。它不读取遗留 `maintenance_thread_id`。TOML 匹配是当前持久配置证据，不证明 UI 标签、计划实际触发或某次运行实际使用的模型。

`status` 刻意分成五部分：`local_control`、`local_binding`、`local_configuration`、`native_automation` 和 `sync_checks`。逐字段检查同时区分“原生值匹配当前本地配置”与“原生值仍匹配上次 bind 的账本快照”；缺字段不是匹配，配置与原生值一起变化也必须重新 bind。每项给出 `true` / `false` / `null`，没有总 `in_sync`，也没有旧的 `schedule_config_in_sync`。原生 TOML 当前没有独立时区证据，因此即使时点相同，`native_schedule_timezone_verified` 仍为 `null`，须按操作流程另行核对宿主时区。

尚未绑定时，保存 agent 配置不发送消息、不切换当前任务，也不创建原生计划。工具返回的本机模型缓存仅是可用性线索，不能替代实际宿主核验。

## 配置变更的应用

对结构化设置使用 `config-show` 读取，再通过 `config-apply --file` 提交 JSON 格式的配置补丁。文件是深合并补丁，不是原生自动化配置。保存前验证；保存后再次读取，核对请求的设置。

| 变更 | 后续动作 |
|---|---|
| 启动窗口 | 下次自动入口读取 |
| 扫描重叠 | 下次扫描使用新范围 |
| agent 模型或推理强度 | 未绑定时只保存；heartbeat 调整固定维护任务，cron 更新并读回同一原生 automation；都不改被重命名任务 |
| 调度时点或时区 | 保存配置后，通过 `automation_update` 更新并读回已绑定原生计划，再按原模式、目标和 automation ID 显式 `bind` 刷新快照 |
| 命名规则、模板、语言或前缀 | 下次正式扫描检测规则变化，重新评估受管理任务 |
| 扩大管理范围 | 下次正式扫描为新增范围补做全量发现 |
| 缩小管理范围 | 停止新处理被排除项，保留账本历史 |

本地配置已保存而原生计划更新失败时，必须报告“配置与调度未同步”，不要把文件写入成功说成整个配置应用成功。保留原生计划的其他字段和通知偏好；不得创建重复计划规避更新失败。尚未绑定原生计划时只保存配置，无需创建计划来完成同步。模型偏好已保存但尚未应用时也要单独说明。

创建、恢复或修改计划前，核对本机实际调度时区是否与配置一致。目前未验证独立时区参数，不一致时暂停 `start` 或计划同步并说明限制；由用户选择调整配置或系统时区，不擅自修改系统时区，也不承诺已按另一时区准点执行。
