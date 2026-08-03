# 邮件模板解耦架构

## 设计目标

模板提取只负责“读”，邮件草稿工具只负责“写”。Agent 不再需要在多个功能重叠的写入工具之间选择。

```text
extract_email_template
        ↓
模板 HTML / 样式壳 / template_ref / To、CC 建议
        ↓
Agent 只把用户的新内容片段放入 body_html
        ↓
compose_email 或 reply_to_email 在本地完整模板中渲染
```

`compose_from_email` 和 `reply_from_email` 已从 Production、Debug MCP 和 CLI 中移除。

## 模板提取规则

### 邮件包含多轮回复或转发历史

系统识别 Outlook、Gmail、`blockquote type="cite"`、`Original Message` 等历史分隔线，只保留正文最上方的第一封可见邮件。

这里的“第一封”是当前 HTML 顶部最新的一封，不是整条会话最早的邮件。历史链不会进入模板。

### 邮件只有一封

如果没有发现任何回复或转发历史分隔线，则完整保留该邮件作为模仿样本，包括：

- `<head>` 与 `<style>`；
- `<body>` 属性；
- 表格与布局；
- 字体、段落和内联 CSS；
- 正文引用的 `cid:` 内联图片信息。

### 超长邮件

EWS 读取上限为 500000 个 HTML 字符：

- 在读取范围内发现历史分隔线：仍只保留分隔线之前的顶部邮件；
- 没发现历史分隔线：将读取到的单封邮件片段平衡标签后作为样本，并返回 warning，不直接阻止流程。

## `extract_email_template` 输出

主要字段：

```text
template_ref
source_message
template_html
template_shell_html
content_markers
suggested_compose_inputs
history_boundary_strategy
quoted_history_excluded
inline_content_ids
warnings
```

`template_shell_html` 中包含明确内容标记。完整模板保存在本地 `template_ref` 中；较大模板只向 Agent 返回预览和精确字符数。Agent 不应根据预览重建完整 HTML。

`template_ref` 默认有效 7 天，只引用本机状态库中的模板资源，不包含用户密码。

## 新邮件流程

```text
1. extract_email_template
2. Agent 只生成新的正文片段
3. compose_email(
       to_queries=用户要求或 suggested_compose_inputs.to_queries,
       cc_queries=用户要求或 suggested_compose_inputs.cc_queries,
       subject=新主题,
       body_html=新的正文片段,
       template_ref=tmpl_xxx
   )
```

`compose_email` 仍是唯一的语义新建邮件草稿工具，并且绝不发送。

## 回复或全部回复流程

```text
1. 确定真正需要回复的 message_ref
2. extract_email_template 可选择同一封或另一封格式样本
3. Agent 只生成新的回复正文片段
4. reply_to_email(
       message_ref=真正的回复目标,
       reply_all=true 或 false,
       body_html=新的回复正文片段,
       template_ref=tmpl_xxx
   )
```

回复目标与模板来源完全独立：

- `message_ref` 决定 EWS Reply/Reply-All 的线程和收件人语义；
- `template_ref` 只提供格式和模板资源；
- 模板历史邮件不会被手工塞入回复正文；
- 原始邮件链由 Exchange 的回复机制附加一次。

## 收件人与附件规则

- 模板提取返回 To、CC 建议，不返回 BCC 建议；
- Agent 是否沿用 To、CC 由用户要求决定；
- 默认只复制渲染后最终 HTML 实际引用的 `cid:` 内联图片；
- 普通模板附件默认不复制；
- 只有显式设置 `copy_template_attachments=true` 才复制普通附件；
- 所有模板资源会在创建草稿前预检，避免留下半成品草稿。

## Agent 路由原则

```text
普通新邮件                    → compose_email
普通回复 / 全部回复           → reply_to_email
用户要求模仿历史邮件格式       → 先 extract_email_template
                                再 compose_email 或 reply_to_email
```

这样 Production Tool surface 仍为 18 个，并且每个工具职责单一。

## v0.7.2 单一正文参数契约

- 没有 `template_ref`：`body_html` 是完整 HTML；
- 有 `template_ref`：`body_html` 是需要注入本地完整模板的新正文片段；
- 不再公开 `template_content_html`，CLI 与 MCP 使用同一套参数语义。
