# 安全策略

[English](SECURITY.md) | **简体中文**

Exchange EWS MCP 会处理邮箱元数据、本地凭据、附件、服务端 HTML 和可写 Exchange 操作。请私下报告安全问题，不要对自己不拥有或不管理的邮箱进行测试。

## 支持版本

安全修复面向当前 `main` 分支和最新正式版本。旧源码快照可能不会获得补丁。

## 报告漏洞

请在 GitHub 仓库的 Security 页面使用 **Report a vulnerability** 创建私有安全公告。

尽量提供：

- 受影响版本或 commit；
- Windows、Python 和 Exchange 环境；
- 影响和真实攻击场景；
- 使用合成或脱敏数据的最小复现；
- 已知缓解建议。

不要包含真实凭据、邮箱正文、邮箱地址、内部主机名、EWS URL、ItemId/ChangeKey、附件数据或个人信息。维护者确认可以披露之前，不要创建公开 Issue。

## 如果秘密已经泄露

立即撤销或轮换相关密码、token 或证书。后续 commit 删除内容并不能从 Git 历史中移除秘密。

## 安全设计

- 密码保存在 Windows 凭据管理器。
- 原始 Exchange 标识隐藏在本地不透明引用之后。
- 普通邮件工作流只创建草稿。
- 会议邀请需要明确确认。
- 附件路径经过 allow-list 和预验证。
- 周报 HTML 始终保存在服务端，Agent 只提交经过转义的文本槽位更新。
- 周报更新要求随机、短时、一次性的 token。
- 创建草稿前会校验源邮件哈希、槽位 manifest 和 HTML 结构。

这些保护不能替代 Exchange 权限、终端安全、邮箱策略、管理员控制和 MCP 客户端安全措施。
