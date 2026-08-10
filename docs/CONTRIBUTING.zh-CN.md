# 参与贡献

[English](CONTRIBUTING.md) | **简体中文**

感谢你改进 Exchange EWS MCP。所有贡献都必须保持“本地优先、草稿优先”的安全模型，并适用于本地部署 Exchange 环境。

## 开始之前

- 先搜索现有 Issue 和 Pull Request，避免重复。
- 普通 Bug、文档和功能建议可以使用公开 Issue。
- 安全问题必须私下报告，见 [SECURITY.zh-CN.md](SECURITY.zh-CN.md)。
- 测试、Issue、提交和日志中不得包含真实密码、邮箱正文、内部 EWS 地址、Exchange ID 或个人信息。

## 开发环境

主要开发平台是 Windows，支持 Python 3.10–3.13。

```powershell
git clone https://github.com/ShermanGu/exchange-ews-mcp.git
cd exchange-ews-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

运行严格测试：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
$env:PYTHONWARNINGS = "default"
.\.venv\Scripts\python.exe -m pytest -W error::ResourceWarning
```

构建发布包：

```powershell
.\.venv\Scripts\python.exe -m build
```

## 修改要求

1. 从 `main` 创建聚焦的功能分支。
2. 不要在同一个 PR 中混入无关重构。
3. 行为变化必须增加或更新确定性 UT。
4. 用户可见变化要同步更新英文和中文入口文档。
5. MCP 工具或路由变化要更新 `AGENT-TOOLS.md`。
6. 真实 Exchange 场景变化要更新 `DT.md`。
7. 保持邮件草稿优先和会议发送确认。
8. 推送前运行完整严格测试和构建。

## 周报功能修改

周报流程的修改还必须满足：

- 不向 Agent 暴露完整 HTML；
- 不允许 Agent 提交标签、偏移或属性；
- 分割线匹配必须失败关闭，并有精确 fixture；
- 保留一次性 token、强制顺序和原子抢占；
- 版面位置只作为提示，不参与实际定位；
- 覆盖表格、非表格、异常 HTML、过期、重复和并发用例；
- 真实行为变化要同步更新 `weekly-report-v06` DT。

## 编码要求

- 支持 Python 3.10 及以上版本。
- 源码和文档统一使用 UTF-8。
- 优先使用明确的工作流边界，避免 Agent 面对多个重叠工具。
- Exchange ItemId、ChangeKey、凭据和服务端 HTML 必须隐藏在本地抽象后面。
- 远端写入前验证所有路径和外部输入。
- 错误信息应可操作，但不能泄露秘密。
- 资源和数据库连接必须确定性关闭。

## 真实 Exchange 验证

UT 不得依赖真实邮箱，应使用 fixture 和 fake client。

需要 DT 时：

- 使用专用测试邮箱；
- 先运行只读 DT；
- 邮件只创建草稿；
- 日历使用 `SendToNone`；
- 检查并清理测试项目；
- 分享报告前完成脱敏。
