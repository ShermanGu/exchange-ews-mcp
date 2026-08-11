# Changelog

## 0.8.3

- Release metadata/docs synchronized to v0.8.3; CI artifact naming and generated distribution checksum manifest are included in the release gate.

- Weekly Report Subject rollover is now server-owned: supported calendar dates and `第N周`/`WKN`/`WN` markers are deterministically advanced by one week during `weekly_report`.
- `weekly_report.subject` now represents the default Subject that will be used for the new draft, not merely the previous source Subject.
- `continue_action.selections.subject` is no longer conditionally required for normal weekly updates; omit it to use the server-selected Subject, and supply it only as an explicit user override.
- Reply All preserves Exchange's native reply Subject when no period marker changed; automatic Subject UpdateItem occurs only for a real rollover or explicit override.
- Year-month-only strings such as `2026-08` are no longer treated as weekly-period markers, avoiding false runtime requirements.

## 0.8.2

- Weekly Report Agent contract compressed to `resume_token/mode/subject/request/slots/history`; dynamic `agent_prompt` and duplicated internal metadata are no longer returned.
- Agent-facing weekly slot IDs are now context-local short IDs (`s1`, `s2`, ...). Long deterministic slot IDs, offsets, HTML paths and hashes remain server-side; `continue_action` translates short IDs back to the hidden manifest before text-only replacement.
- Weekly `changes[]` now uses compact `{"id":"s1","text":"..."}` entries. Invalid local IDs remain retryable with the same unconsumed token.
- Weekly history is fixed to three weeks total: the newest week is represented by editable `slots`, and only the previous two weeks are returned in `history`. The Agent-facing `weekly_report` tool no longer exposes `max_reports`.
- Empty slot locations are omitted instead of returning `null`; `loc` remains advisory and never participates in deterministic write positioning.

## 0.8.1

- 修复 Weekly Report continuation 的 token 消耗时机：`slot_id`、`subject`、`changes` 等确定性 Agent 参数校验现在发生在 `context_ready -> applying` 原子抢占之前。
- Agent 提交错误或旧的 `slot_id` 时，不再把有效 `resume_token` 标记为 `failed`；修正 selections 后可直接使用同一个 token 重试 `continue_action`。
- 保留 token 的严格上下文绑定、30 分钟 TTL、同源上下文 supersede、并发原子抢占、source/body stale 检查和成功写入的一次性语义。
- 改进周报 Agent Prompt 与错误提示，明确只有 `context_stale`、过期、已使用或被新上下文取代时才重新调用 `weekly_report`。

## 0.8.0

- 周报 Agent 工具面收敛为单一 `weekly_report` 入口；第二步统一通过 `continue_action`，内部 `update_weekly_report` 不再注册给 Agent。
- 删除 Outlook separator 白名单依赖；改为扫描可见文本中的首个 `发件人` / 独立英文 `From`，并回退到扫描根下 depth-0 HTML 块后截断 quoted history。
- 周报历史改为搜索最近最多五封匹配邮件，并从每封邮件各提取顶部当前正文；兼容 Reply All 长线程和每周新建邮件两种习惯。
- Server 自动选择周报草稿模式：发现 quoted-history sender header 时 Reply All，否则 Compose；Compose 复制源周报 To/CC。
- Subject 含明显日期/周次时，`continue_action` 必须提交更新后的 Subject，避免旧周期静默继承。
- 保留 Server-owned HTML、slot manifest/offset、结构签名、location-only-as-hint、一次性 token 与 stale-source 防护。

## 0.7.0

- Stabilized compact routing before the next weekly-report refactor: `continue_action` now uses explicit mail/calendar action allow-lists and rejects unknown action types instead of guessing a workflow.
- Mail search/list results now emit exactly one typed reference: drafts receive only `draft_ref`, normal messages receive `message_ref`; drafts can no longer accidentally become reply/forward sources.
- Draft editing validates reference kind before attachment path preflight, including attachment-only calls, so `calendar_ref` is routed safely without any file or Exchange side effects.
- Removed Room/Resource availability support from public/runtime validation because room/resource mailbox lookup is not supported in the target deployment; person availability remains Organizer/Required/Optional only.
- Fixed the Windows CI matrix by keeping third-party warnings visible while treating only `ResourceWarning` as fatal across Actions, local unit tests, and release checks.
- Updated package license metadata to the current SPDX format and removed the corresponding setuptools deprecation warnings.
- Replaced the overlapping 21-tool Production surface with a compact semantic facade. The stabilized surface contains 12 tools: `search_mail`, `read_mail`, independent `resolve_people`, `save_mail_draft`, `edit_mail_draft`, `continue_action`, the two weekly-report tools, `read_calendar`, `find_meeting_times`, `save_meeting`, and `send_meeting_invitation`.
- Consolidated list/search/semantic mail discovery into `search_mail`, including unread, attachment, pagination, person-resolution, and resumable ambiguity behavior.
- Consolidated compose/reply/Reply All/forward into the draft-only `save_mail_draft`; consolidated draft field updates and prevalidated attachments into `edit_mail_draft`.
- Consolidated calendar list/item reads into `read_calendar`, raw availability into `find_meeting_times`, and unsent meeting create/update operations into `save_meeting`.
- Kept meeting invitation sending as a separate `confirm_send=true` operation and kept the two-step weekly-report token/HTML safety contract unchanged.
- Kept the Production tool-definition surface compact while restoring independent semantic people resolution; low-level `resolve_names` remains debug-only.
- Debug now exposes the compact Production facade plus six low-level write primitives, for 18 tools total.

## 0.6.16

- Fixed `update_meeting` rejecting some valid unsent meetings when on-premises Exchange reports `IsMeeting=false` despite retained attendees. Meeting classification now uses attendee collections and MCP reference provenance as corroborating evidence.
- Added explicit `reference_kind`, `update_tool`, and `send_tool` hints to meeting results so Agents do not confuse `calendar_ref` with `draft_ref`.
- Changed accidental `update_email_draft(draft_ref=cal_...)` calls into a non-mutating structured routing response that recommends `update_meeting`, instead of surfacing a tool execution error.
- Enriched calendar references with meeting/attendee metadata for resilient follow-up updates and sends.
- Preserved UTF-8 CLI output compatibility on Windows CI and corrected the uploaded distribution artifact name to v0.6.16.

## 0.6.15

- 修复会议发送确认分支：`confirm=save`、`confirm=no` 和“不发送，仅保存”现在会真正创建 `SendToNone` 日历项目，不再重复返回 `needs_confirmation`。
- 新增 Production 工具 `update_meeting`，可修改尚未发送会议的主题、正文、时间、地点、参会人和提醒，并保持不通知参会人。
- 新增 Production 工具 `send_meeting_invitation`，要求 `confirm_send=true`，使用当前服务器 ChangeKey 向所有参会人发送邀请并保存已发送副本。
- `calendar_ref` 更新/发送前会按 ItemId 获取服务器最新 ChangeKey，可兼容用户先在 Outlook 中手动编辑会议。
- Production / Debug 工具面更新为 21 / 27。

## v0.6.14 release-check build hotfix

- 修复 `run-release-check.cmd` 在已安装 `build` 时触发 PEP 517 隔离构建，进而在企业内网或离线环境中报 `BackendUnavailable: Cannot import setuptools.build_meta` 的问题。
- 发布检查现在显式使用当前已选 Python 的本地 `setuptools.build_meta` 与 `wheel`，执行 `python -m build --no-isolation`；缺少构建后端时会给出明确的修复命令。

## 0.6.14

- 修复 `run-release-check.cmd` 固定使用 `.venv\Scripts\python.exe` 的问题：现在优先选择当前命令行中可成功执行 `python -m pytest` 的解释器，并提供虚拟环境和 `py -3` 回退，避免虚拟环境未安装 pytest 时误报。
- 整理正式仓库目录：根目录只保留英文和中文 README，其他说明文档统一放入 `docs/`，并同步修复全部相对链接、发布检查与仓库契约测试。
- 统一所有自动化脚本的 pytest 启动方式为 Python 模块调用（`python -m pytest` 或当前解释器等价形式），避免 PATH 指向错误的 pytest 可执行文件。
- 正式发布仓库结构补齐：新增双语 README、架构、周报、DT、安装升级、贡献、安全、行为准则、Issue/PR 模板和 Windows GitHub Actions CI。
- 新增正式 `weekly-report-v06` 真实 Exchange DT 分组：只读模式验证周报上下文、紧凑槽位和完整 Prompt；完整模式仅创建一封不发送的原生 Reply All 测试草稿并记录引用供人工清理。
- 新增统一发布检查入口 `run-release-check.cmd` / `scripts/release_check.py`，验证版本、19/25 工具面、五组 DT、必备仓库文件、严格 UT、源码编译和分发包构建。
- 修正文档与 CLI 中遗留的工具数量和旧 DT 版本说明；发布文档明确区分确定性 UT/模拟 DT 与必须在用户 Windows/Exchange 环境执行的真实 DT。
- 修复紧凑 `location` 过早返回 `semantic_location` 的问题：完整布局分析已经取得但未达到强表头阈值的第二层 Outlook `td` 表头、上下左右邻格和嵌套信息不再被静默丢弃。
- `location` 仍是单一可空字符串，但现在尽量拼接逻辑行列、行表头、多级列表头、明确标注的表头候选、邻格、外层单元格、同格文本节点和非表格块级上下文；无法形成有意义的位置说明时保持 `null`。
- 标准 `th` 多级表头直接输出完整层级；普通 `td` 视觉表头以“候选”标记输出，避免将低置信度推断伪装成确定事实。
- Agent Prompt 新增日期硬校验：逐一检查所有含日期、日期范围、星期、周次或月份的槽位，继承的周报周期日期必须更新；无法区分周期日期与事实日期时禁止猜测并要求先确认。
- 新增 Outlook `td` 双表头、完整位置字符串、日期 Prompt 规则和 640 字符边界回归测试。

## 0.6.13

- Production `get_weekly_report_context` 改为紧凑响应：每个槽位只返回 `slot_id`、`text` 和可空的 `location`，不再返回完整 `layout_context`、前后文本、HTML 路径、坐标、邻格或置信度等内部字段。
- 复杂表格、合并单元格、多级表头、嵌套表格、标题、段落和列表的完整分析仍在 Server 内部执行，只把最终位置摘要压缩到 `location`；分析失败时 `location=null`，不影响确定性文本替换。
- Agent Prompt 的核心规则、周报化改写要求、正反示例、用户输入、参考材料和最多五周历史全文全部保留，不做摘要或规则删减。
- 为避免重复占用上下文，Prompt 不再内嵌槽位 JSON，而是直接引用同一次工具结果中的 `editable_slots`；Production 顶层也不再重复返回 `latest_report_text`、`historical_reports`、`reference_materials`、`layout_summary` 和 `editing_contract`。
- 新增紧凑 schema、位置为空、位置长度上限、布局分析降级及响应体积测试；20 行复杂表格的槽位 JSON 小于完整内部版面 payload 的 10%。

## 0.6.11

- `update_weekly_report` 的 `changes[]` 简化为只接收 `slot_id` 与 `new_text`，移除容易被模型抄错的 `expected_text`。
- Server 根据一次性 `weekly_flow_token` 保存的原始模板和槽位清单自行取得旧文本；源邮件 Body 哈希、槽位 manifest、稳定 slot_id、上下文过期检测和 HTML 结构复核继续提供并发与完整性保护。
- Agent Prompt 与工具 schema 明确禁止重复提交旧文本，旧版 `expected_text` 字段作为未知字段在 EWS 写入前拒绝。
- 增加无 `expected_text` 正常更新、旧字段拒绝、实体保持、未知/重复槽位和一次性 token 回归测试。

## 0.6.10

- 周报两步流程改为服务端强制状态机：`get_weekly_report_context` 每次生成随机 `weeklyflow_*` token，30 分钟有效且只能使用一次。
- `update_weekly_report` 通过 SQLite 条件更新原子地将状态从 `context_ready` 切换为 `applying`；重复、并发、过期、已完成、失败或被新上下文替代的 token 均在 EWS 写入前拒绝。
- 同一最新周报重新获取上下文时，旧的未使用 token 自动标记为 `superseded`；同一范围正在 `applying` 时禁止创建第二个流程。
- 更新成功后 token 标记为 `completed`；上下文过期标记为 `context_stale`；校验失败标记为 `failed`，必须重新获取上下文。
- 重写周报 Agent Prompt：要求先理解用户口语事实，再优化为简洁、正式、客观的周报书面表达；禁止机械复制、状态夸大和事实虚构。
- 增加一次性、并发抢占、过期、旧 token 淘汰、重复调用拒绝和 Prompt 润色规则回归测试。

## 0.6.9

- 周报编辑从“Agent 返回完整 HTML”改为稳定文本槽位：`get_weekly_report_context` 不再向 Agent 暴露模板 HTML，只返回最多五周纯文本和最新周报的 `editable_slots`。
- 每个槽位包含稳定 `slot_id`、当前纯文本、HTML 路径和相邻文本上下文，便于 Agent 理解项目、日期和表格位置。
- `update_weekly_report` 改为接收 `changes[]`，每项包含 `slot_id`、`expected_text`、`new_text`；未提交槽位原样继承。
- Server 对 `new_text` 做 HTML 转义，并按原始偏移从后向前写回保存的模板；Agent 无法修改标签、属性、样式、表格、图片或注释。
- 增加槽位清单签名、重复/过期槽位拒绝、expected_text 并发保护、空白与 `&nbsp;` 原样保留、受保护标签文本排除和结构签名复核。
- 生成后的 HTML 仍只通过一次原生 Reply All 进入 `NewBodyContent`，不创建临时周报草稿，也不调用 `UpdateItem Body`。

## 0.6.8

- 周报生产工具改为 `get_weekly_report_context` 与 `update_weekly_report` 两步；移除 Agent 可见的 `prepare_weekly_report` 临时草稿流程。
- 上下文工具只读提取最新 HTML 模板，并按顶层白名单分割线返回最多五周完整纯文本；连续 1～2 个分割块自动合并为空边界。
- 支持把用户本周输入和 Agent 已读取的可选文件纯文本放入完整 Agent Prompt，要求全量比较五周变化，未提及内容继承最新一周。
- Agent 只能返回修改后的完整 HTML；标签、属性、注释和顺序必须与模板逐字符一致，只允许文本槽变化。
- HTML 完整性通过后，最终 HTML 直接作为最新周报原生 Reply All 的 `NewBodyContent`，不创建临时周报草稿，也不执行 `UpdateItem Body`。
- 保留 CID 内嵌附件补齐、Subject 单独更新、上下文过期检测和全量回归测试。

## 0.6.7

- 将周报回复分割线收敛为独立白名单，候选 `<p>...</p>` 原始字符串必须与白名单条目完全一致；不再做字体、语言、实体或空白的语义兼容。
- 只有 `div.WordSection1` 的直属 `<p>` 子块可以作为分割线；位于 table、td、嵌套 div 等更深层容器中的相同白名单块全部忽略。
- 增加顶层/嵌套白名单边界、近似但非完全一致块、字符阈值和诊断脚本回归测试。

## 0.6.6

- 修复 `WordSection1` 开头普通空白 `MsoNormal` 被误判为周报分割线的问题。
- 分割线现在必须在嵌套 `span` 的 `style` 中包含已确认的 `font-family`；仅有 `lang=EN-US` 不再放行。
- 增加周报边界诊断脚本与回归测试。

## 0.6.5

- `prepare_weekly_report` 的 WordSection1 提取逻辑推翻重写：不再识别或跳过 From/Sent/To/Cc/Subject 邮件头，也不再采用 UniqueBody、标准回复边界或重复分隔线作为备用。
- 固定从第一个 `div.WordSection1` 开始标签之后复制原始 HTML，到第一个受支持的 `MsoNormal/span/o:p/&nbsp;` 周报分隔块之前停止，并将该片段直接作为原生 Reply All 的 `NewBodyContent`。
- 找不到 `WordSection1` 时立即拒绝；从 WordSection1 内部起点到第一个有效分隔块超过 500000 字符时立即拒绝，避免复制整条历史。
- 分隔块兼容单引号、双引号、无引号属性；中文字体名使用 UTF-8 源码、EWS XML 编码解码、HTML entity 解码和 Unicode NFKC 规范化，支持 `等线` 直接文本及数字实体。
- 新增真实边界、首个分隔块、字符阈值、缺失 WordSection1、缺失分隔块、HTML 标签补齐和 Reply All 参数专项测试。

## 0.6.4

- 修复 `install.ps1` 仍硬编码期望版本 0.6.2，导致安装 0.6.3 后自检失败的问题。
- 将运行时版本统一收敛到 `exchange_ews_mcp.__version__`：wheel 元数据、EWS User-Agent、DT marker 与 DT report 不再各自硬编码。
- 安装脚本从源码版本文件读取期望版本，并同时校验模块版本、distribution metadata 和实际安装路径。
- 新增发布一致性测试，防止旧版本号再次残留在运行时代码或安装脚本中。

## 0.6.3

- `prepare_weekly_report` 新增与 `search_emails` 一致的 `folder` / `folders` 输入语义；`folders` 设置时覆盖 `folder`。
- 所有邮件搜索路径共用文件夹规范化：支持 `inbox`、`sentitems` 等 EWS id，以及收件箱、已发送邮件、Sent Items、草稿箱等常见中英文 Outlook 名称，统一转成标准 id 并去重。
- WordSection1 邮件头仍只跳过连续的 4/5 个 `MsoNormal` 块，不按数量盲删；标签支持简体中文、繁体中文和英文。
- 头部文本使用 UTF-8 源码、EWS XML 字节解析、HTML entity 解码、Unicode NFKC 规范化，并移除 Outlook 常见零宽/双向控制字符。
- 周报分隔结构支持中文 Office 字体（等线/DengXian/宋体等）和英文 Office 字体（Aptos/Calibri/Arial 等），并记录匹配语言与变体。
- 新增诊断字段：`word_section_header_count`、`word_section_header_labels`、`word_section_separator_variant`、`word_section_separator_language`、`search_folders`。
- 新增文件夹别名、中英文邮件头、HTML 实体、全角标点和中英文分隔结构测试；全量测试 135 项通过。

## 0.6.2

- `prepare_weekly_report` 在原始 HTML 存在 `div.WordSection1` 时，不再优先采用 `UniqueBody`。
- 严格识别 WordSection1 开头的 4 个或 5 个 Outlook 邮件头段落：发件人、发送时间、收件人、可选抄送、主题。
- 从主题段落结束后开始复制 wk3 正文，并在第一个指定的 `MsoNormal/o:p/&nbsp;` 周报分隔结构前停止。
- 复制结果保留原始 `WordSection1` 开始标签，只补齐切片造成的缺失结束标签，不进行 DOM 重新序列化。
- 新增诊断字段：`word_section_offset`、`word_section_header_fields`、`word_section_separator_count`。
- 新增含抄送、无抄送、错误邮件头结构和 Reply All 实际复制行为测试。

## 0.6.1

- 修复周报完整 Body 已由 EWS 返回、却被 MCP 本地 500000 字符上限截断并提前拒绝的问题；周报内部读取现在不做本地字符截断。
- 将“本地输出截断”和 Exchange `IsTruncated` 服务器标志分开记录；只有服务器确实返回不完整 Body 时才拒绝。
- `UniqueBody` 被服务器标记截断时不再终止 prepare，而是忽略该字段并继续从完整 Body 提取。
- 新增用户实际 Outlook HTML 的重复 `MsoNormal / o:p / &nbsp;` 分隔结构识别，从第一次重复分隔位置切出 wk3。
- 字符串切割后只追加缺失的结束标签，不使用 DOM 重新序列化，不修改表格属性、VML、条件注释或已有 HTML。
- 新增超过 500000 字符正文、重复 Mso 分隔、伪完整线程 UniqueBody、标签补齐和无本地上限读取测试；全量测试 127 项通过。

## 0.6.0

- 以 v0.5.10 为干净基线新增 `prepare_weekly_report` 和 `update_weekly_report`，未引入通用 template 参数。
- `prepare_weekly_report` 只引用最新周报执行一次原生 Reply All，并将可靠提取出的最新一周正文放入 `NewBodyContent`。
- 最新一周正文优先使用经过验证的 `UniqueBody`；必要时按 Outlook/OWA 回复边界对原始 HTML 做字符串切片。无法可靠切割时在创建草稿前失败，禁止把完整历史作为兜底。
- `update_weekly_report` 读取同一个草稿，按稳定 `segment_id` 只替换选中的顶部可见文本节点；未选文本、表格、图片引用和回复历史保持不变。
- prepare 使用不可见边界标记追踪顶部编辑区，并保存历史后缀签名；边界缺失或历史变化时拒绝更新。
- 支持复制顶部正文引用但 Reply All 草稿未自动保留的 CID 内嵌附件，同时避免重复复制已有 ContentId。
- Production profile 增至 19 个工具，Debug profile 增至 25 个工具。
- 新增状态化 Exchange 行为测试、SOAP XML 测试和文本局部替换测试；全量测试 122 项通过。

## 0.5.10

- 发布前审计扩展至 108 项 UT，并以 `ResourceWarning` 作为错误运行全套测试。
- 修复 `ReferenceStore` 的 SQLite 连接未主动关闭问题，避免长时间运行的 Agent 积累文件句柄。
- `compose_email` 在创建草稿前预校验全部附件，避免后续附件非法时留下半成品草稿。
- `continue_action` 在邮件候选确认阶段限制所选 `message_ref` 必须来自本次候选集合。
- 新增全部 23 个 server wrapper 的参数转发测试，以及多步骤“选会议时间 → 二次确认发送”测试。
- 新增 `RELEASE-AUDIT.md` 和 `AGENT-CONNECTION.md`。

## 0.5.9

- 当前用户的 MCP 本地工作时间覆盖 Exchange 邮箱 WorkingHours，用于 availability 展示和排会算法。
- Exchange 原始 WorkingHours 保留在 `exchange_working_hours`。
- 其他参会人仍使用 Exchange 返回的 WorkingHours；缺失时使用 fallback。

## 0.5.8

- 配置工作时间兼容 `H:MM` 和 `HH:MM`，例如 `9:30` 与 `09:30`。
- 保存和读取时统一规范化为 `HH:MM`。

## 0.5.7

- 默认 Agent server 收敛为 17 个 Production 工具。
- 新增完整 23 工具的 Debug server。
- 新增 `tool-list`、`mcp-config`、`AGENT-TOOLS.md` 和 `FRESH-START.md`。

## 0.5.6

- WorkingHours 使用直观结构呈现时区、夏令时和工作时间。
- 无夏令时的全零 Standard/Daylight 占位规则规范化为 `null`。
- 共同空闲算法统一使用规范化 WorkingHours。

## 0.5.5

- 修复部分本地 Exchange 返回无 `Z` 的忙闲事件时间时被误判的问题。
- EWS 无偏移事件时间按 UTC 规范化。

## 0.5.4

- 日历入口支持无偏移本地时间，并按用户 IANA 时区解释。
- 保留 UTC 传输，同时返回 UTC 和本地展示字段。
- 增加夏令时歧义时间与不存在时间保护。

## 0.5.3

- 明确 UTC 内核与本地时区展示职责。
- 支持已知会议室邮箱以 `Room` 类型查询忙闲。

## 0.5.2

- GetUserAvailability 改用 `TimeZoneContext/TimeZoneDefinition Id="UTC"`。
- 删除不兼容的旧式 Bias/StandardTime/DaylightTime 请求结构。

## 0.5.1

- 增加 Windows 所需的 `tzdata` 依赖。
- `UTC`、`Etc/UTC`、`GMT` 增加无时区数据库兜底。

## 0.5.0

- 新增忙闲查询、日历读取、会议创建和共同空闲算法。
- 新增 `find_meeting_times`、`schedule_meeting` 和 `calendar-v05` DT。

## 0.4.2

- 拼音查询收集全部公司域名中的同组邮箱候选。
- 只有一个候选存在历史来往时才自动选择；多个历史联系人必须确认。

## 0.4.1

- 安装器强制重新安装当前目录源码并校验版本与包路径。

## 0.4.0

- 新增 Semantic Mail Workflow、人员历史消歧和 `continue_action`。

## 0.3.1

- 统一 DT 为持续增长的分组测试框架。

## 0.3.0

- 人员查询限定为姓名拼音或完整邮箱。
- Production Workflow Primitives 和本地引用状态稳定化。
