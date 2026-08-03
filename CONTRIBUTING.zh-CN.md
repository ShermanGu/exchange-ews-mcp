# 参与贡献

[English](CONTRIBUTING.md) | **简体中文**

感谢你参与改进 Exchange EWS MCP。所有贡献都应保持本项目“本地优先、草稿优先”的安全模型，并适用于本地部署 Exchange 环境。

## 开始之前

- 提交前请先搜索已有 Issue 和 Pull Request，避免重复。
- Bug、文档问题和功能建议可以使用公开 Issue。
- 涉及安全的问题请使用 GitHub 私密漏洞报告，详见 [SECURITY.zh-CN.md](SECURITY.zh-CN.md)。
- 不要在 Issue、测试或提交中加入真实邮件内容、账号密码、内部服务器名称、Exchange 标识或个人数据。

## 开发环境

主要开发平台为 Windows，要求 Python 3.10 或更高版本。

```powershell
git clone https://github.com/ShermanGu/exchange-ews-mcp.git
cd exchange-ews-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

运行严格测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -W error::ResourceWarning
```

## 修改流程

1. 从 `main` 创建职责单一的分支。
2. 不要把无关重构混入同一个 Pull Request。
3. 行为变化必须新增或更新测试。
4. 面向用户的行为变化应同时更新英文和简体中文入口文档。
5. 除非明确属于破坏性变更，否则保持现有工具名兼容。
6. 推送前运行完整严格测试。

## 编码要求

- 支持 Python 3.10 及以上版本。
- 优先使用职责清晰的工作流，不增加功能重叠的 Agent 工具。
- 邮件写入保持“只创建草稿”，会议发送必须显式确认。
- Exchange ItemId、ChangeKey、凭据和完整模板必须保留在本地抽象之后。
- 创建远程状态前先验证附件和其他外部输入。
- 错误信息应可操作，但不得泄漏敏感信息。
- 源码和文档统一使用 UTF-8。

## EWS 相关测试

单元测试不能依赖真实邮箱。协议和工作流行为应使用确定性的 Fixture 与 Fake Client。

如果修改还需要真实 Exchange 验证：

- 使用专用测试邮箱；
- 只创建草稿，不发送邮件；
- 未经显式确认不要发送会议邀请；
- 分享结果前删除或脱敏日志中的邮箱数据。

## Pull Request 要求

一个合格的 Pull Request 应包含：

- 问题与解决方案的简洁说明；
- 对用户和兼容性的影响；
- 新增或更新的测试；
- 实际验证命令和结果；
- 必要的文档更新；
- 不包含凭据、邮件内容、虚拟环境、缓存或本地状态文件。

维护者可能会要求缩小范围、补充回归测试或同步翻译后再合并。
