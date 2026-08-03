# Agent E2E：从第三大测试继续

先发给 Agent：

> 从模板功能继续验收。模板提取必须优先使用 EWS UniqueBody。不要要求 MCP 把完整超长 HTML 放进 Tool 返回；完整模板应保存在 template_ref。后续创建新邮件或回复时，传 template_ref，并把仅含新内容的片段放入 body_html。除非我明确确认，邮件只创建草稿，会议不发送邀请。每步报告调用的 Tool、status、Ref、truncation 字段和 sent。

## 14. 多轮回复只取当前第一封

> 在 Sent Items 中找到主题包含 `<TEMPLATE_REPLY_KEYWORD>`、正文含多轮回复历史的最新邮件。调用 extract_email_template，只读不建草稿。请报告 template_ref、unique_body_available、unique_body_type、unique_body_truncated、history_boundary_strategy、quoted_history_excluded、template_html_chars、template_html_preview_truncated 和 warnings。不要把 preview 当作完整模板。

通过标准：

- `history_boundary_strategy` 优先为 `ews_unique_body`；
- `unique_body_available=true`；
- `template_ref` 存在；
- 后续历史不在当前模板内容中；
- 即使完整 Body 很长，也不能因为约 5000 字符的 Agent 输出限制而误判模板阶段。

## 15. 单封邮件完整模仿

> 在 Sent Items 中找到主题包含 `<TEMPLATE_SINGLE_KEYWORD>`、没有回复历史的独立 HTML 邮件，调用 extract_email_template。报告 template_ref、unique_body_available、quoted_history_excluded、template_html_chars、template_shell_strategy 和 inline_content_ids。不要创建草稿。

通过标准：`quoted_history_excluded=false`，表格、签名、样式和内联资源仍保存在 template_ref 中。

## 16. 用模板创建新邮件

> 使用刚才的 template_ref 创建新 HTML 草稿。调用 compose_email，收件人沿用 suggested_compose_inputs 的 To/CC，不使用 BCC，主题为 `[E2E] 模板新邮件`。body_html 只传新的正文片段，内容包含“本周进展、风险、下周计划”三个区块，不要传完整模板。普通附件不复制，不发送。

通过标准：

- Tool 是 `compose_email`；
- 使用 `template_ref + body_html 新正文片段`；
- 返回 `template_render_strategy=explicit_content_markers`；
- `sent=false`。

## 17. 模板与回复目标解耦

> 回复目标是在 Inbox 中主题包含 `<KNOWN_INBOX_KEYWORD>` 的最新邮件；格式来源使用刚才的 template_ref。调用 reply_to_email，设置 reply_all=true，body_html 只传新的回复正文片段，不传完整模板。回复收件人必须来自目标邮件，不能来自模板邮件。不要发送。

通过标准：

- `message_ref` 决定线程和 Reply-All；
- `template_ref` 只决定格式资源；
- 返回 `template_render_strategy=explicit_content_markers`；
- 原始历史只由 Exchange 附加一次；
- `sent=false`。

## 18 及以后

继续执行原 v0.7 E2E 文档中的日历测试、负向测试和最终覆盖检查。模板相关步骤一律使用上述新调用方式。
