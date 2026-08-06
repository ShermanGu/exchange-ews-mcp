from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import service
from .tool_profiles import tool_names

def get_current_user() -> dict[str, Any]:
    """返回当前认证用户的显示名、主 SMTP 邮箱和 person_ref。

    如果无法从 NTLM 用户名唯一推断，会返回 ambiguous/not_found，并提示先用 CLI
    set-current-user 显式配置。不会暴露或返回密码。
    """
    return service.get_current_user()


def resolve_names(
    query: str,
    limit: int = 20,
) -> dict[str, Any]:
    """使用姓名拼音或完整邮箱查询个人联系人和全球通讯簿。

    不接受中文姓名查询。若 query 含非 ASCII 字符，将返回
    romanized_query_required，由主 Agent 请求用户提供拼音或邮箱地址。
    """
    return service.resolve_names(query=query, limit=limit)


def create_draft(
    to: list[str],
    subject: str,
    body_html: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
) -> dict[str, Any]:
    """在当前用户的 Exchange 草稿箱中创建 HTML 富文本邮件，但不发送。

    返回 draft_ref；后续优先使用 draft_ref 修改草稿或添加附件。
    """
    return service.create_draft(
        to=to, cc=cc, bcc=bcc, subject=subject, body_html=body_html
    )


def list_emails(
    folder: str = "inbox",
    limit: int = 20,
    offset: int = 0,
    unread_only: bool | None = None,
) -> dict[str, Any]:
    """按时间倒序列出邮箱中的邮件摘要并返回 message_ref/draft_ref。"""
    return service.list_emails(
        folder=folder, limit=limit, offset=offset, unread_only=unread_only
    )


def search_emails(
    folder: str = "inbox",
    folders: list[str] | None = None,
    subject_contains: str | None = None,
    sender: str | None = None,
    to_contains: str | None = None,
    cc_contains: str | None = None,
    participant_contains: str | None = None,
    unread_only: bool | None = None,
    has_attachments: bool | None = None,
    conversation_id: str | None = None,
    internet_message_id: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """搜索邮件摘要并返回稳定的 message_ref。

    folders 可跨多个标准文件夹搜索；设置 folders 时忽略 folder。
    participant_contains 会匹配发件人、To 或 CC；多个条件使用 AND。
    """
    return service.search_emails(
        folder=folder,
        folders=folders,
        subject_contains=subject_contains,
        sender=sender,
        to_contains=to_contains,
        cc_contains=cc_contains,
        participant_contains=participant_contains,
        unread_only=unread_only,
        has_attachments=has_attachments,
        conversation_id=conversation_id,
        internet_message_id=internet_message_id,
        after=after,
        before=before,
        limit=limit,
        offset=offset,
    )


def get_email(
    item_id: str | None = None,
    message_ref: str | None = None,
    draft_ref: str | None = None,
    change_key: str | None = None,
    max_body_chars: int = 50000,
) -> dict[str, Any]:
    """读取一封邮件的正文、收发件人和附件元数据。

    item_id、message_ref、draft_ref 必须且只能提供一个。Workflow 应优先使用 ref。
    """
    return service.get_email(
        item_id=item_id,
        message_ref=message_ref,
        draft_ref=draft_ref,
        change_key=change_key,
        max_body_chars=max_body_chars,
    )


def reply_as_draft(
    body_html: str,
    reply_all: bool = False,
    item_id: str | None = None,
    message_ref: str | None = None,
    change_key: str | None = None,
) -> dict[str, Any]:
    """基于原邮件创建回复或全部回复草稿，但不发送。

    item_id/message_ref 二选一；优先使用 message_ref。ChangeKey 自动刷新。
    """
    return service.reply_as_draft(
        item_id=item_id,
        message_ref=message_ref,
        body_html=body_html,
        reply_all=reply_all,
        change_key=change_key,
    )


def forward_as_draft(
    to: list[str],
    body_html: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    item_id: str | None = None,
    message_ref: str | None = None,
    change_key: str | None = None,
) -> dict[str, Any]:
    """基于原邮件创建转发草稿，但不发送；优先使用 message_ref。"""
    return service.forward_as_draft(
        item_id=item_id,
        message_ref=message_ref,
        to=to,
        cc=cc,
        bcc=bcc,
        body_html=body_html,
        change_key=change_key,
    )


def update_draft(
    item_id: str | None = None,
    draft_ref: str | None = None,
    change_key: str | None = None,
    subject: str | None = None,
    body_html: str | None = None,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    importance: str | None = None,
) -> dict[str, Any]:
    """修改现有草稿的收件人、主题、HTML 正文或重要性，绝不发送。

    item_id/draft_ref 二选一；优先使用 draft_ref。空列表可清空对应收件人字段。
    """
    return service.update_draft(
        item_id=item_id,
        draft_ref=draft_ref,
        change_key=change_key,
        subject=subject,
        body_html=body_html,
        to=to,
        cc=cc,
        bcc=bcc,
        importance=importance,
    )


def add_attachment_to_draft(
    file_path: str,
    item_id: str | None = None,
    draft_ref: str | None = None,
    change_key: str | None = None,
    attachment_name: str | None = None,
) -> dict[str, Any]:
    """把允许目录中的本地文件添加到现有草稿，绝不发送。

    item_id/draft_ref 二选一；优先使用 draft_ref。
    """
    return service.add_attachment_to_draft(
        item_id=item_id,
        draft_ref=draft_ref,
        file_path=file_path,
        change_key=change_key,
        attachment_name=attachment_name,
    )



def resolve_people(
    query: str,
    limit: int = 100,
    lookback_days: int = 365,
    auto_select: bool = True,
) -> dict[str, Any]:
    """使用姓名拼音或完整邮箱解析人员，并结合历史邮件消歧。

    裸拼音会收集所有已配置公司域名下、邮箱 local-part 以该拼音开头的候选；
    不会优先选择无数字后缀的邮箱。多个候选中若只有一个存在历史来往才自动选择。
    """
    return service.resolve_people(
        query=query,
        limit=limit,
        lookback_days=lookback_days,
        auto_select=auto_select,
    )


def compose_email(
    to_queries: list[str],
    subject: str,
    body_html: str,
    cc_queries: list[str] | None = None,
    bcc_queries: list[str] | None = None,
    attachments: list[str] | None = None,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """解析收件人并创建邮件草稿，绝不发送。

    查询只使用姓名拼音或完整邮箱。存在无法自动消歧的收件人时，
    返回 resume_token，由 continue_action 接收用户选择后继续创建草稿。
    """
    return service.compose_email(
        to_queries=to_queries,
        cc_queries=cc_queries,
        bcc_queries=bcc_queries,
        subject=subject,
        body_html=body_html,
        attachments=attachments,
        lookback_days=lookback_days,
    )


def find_email(
    folders: list[str] | None = None,
    sender_query: str | None = None,
    participant_query: str | None = None,
    subject_contains: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 20,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """使用结构化语义条件定位邮件，并隐藏 ItemId/ChangeKey。

    主 Agent 负责把“上周”“昨天”等自然语言转换为 after/before。
    唯一结果返回 selected_message；多个结果返回 message_ref 候选。
    """
    return service.find_email(
        folders=folders,
        sender_query=sender_query,
        participant_query=participant_query,
        subject_contains=subject_contains,
        after=after,
        before=before,
        limit=limit,
        lookback_days=lookback_days,
    )


def reply_to_email(
    body_html: str,
    reply_all: bool = False,
    message_ref: str | None = None,
    folders: list[str] | None = None,
    sender_query: str | None = None,
    participant_query: str | None = None,
    subject_contains: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 20,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """按 message_ref 或搜索条件定位原邮件并创建回复草稿，绝不发送。"""
    return service.reply_to_email(
        body_html=body_html,
        reply_all=reply_all,
        message_ref=message_ref,
        folders=folders,
        sender_query=sender_query,
        participant_query=participant_query,
        subject_contains=subject_contains,
        after=after,
        before=before,
        limit=limit,
        lookback_days=lookback_days,
    )


def get_weekly_report_context(
    user_input: str,
    reference_materials: list[dict[str, str]] | None = None,
    subject_contains: str = "周报",
    folder: str = "sentitems",
    folders: list[str] | None = None,
    lookback_days: int = 60,
    max_reports: int = 5,
) -> dict[str, Any]:
    """每次新的周报更新请求都必须首先调用本工具。

    读取最多五周周报纯文本，内部完成复杂表格与非表格版面分析，但每个
    可编辑槽位只返回 slot_id、text 和可空的精简 location，以避免撑爆 Agent
    上下文；完整 Agent Prompt 与周报化改写规则不压缩。同时返回随机、30 分钟
    有效、只能使用一次的 weekly_flow_token。即使当前会话中存在旧
    token，也必须针对用户这一次输入重新调用本工具；新上下文会使旧的未用
    token 失效。reference_materials 是 Agent 已读取的可选文件纯文本。
    本工具不创建 Exchange 草稿，也不把 HTML 暴露给 Agent。
    """
    return service.get_weekly_report_context(
        user_input=user_input,
        reference_materials=reference_materials,
        subject_contains=subject_contains,
        folder=folder,
        folders=folders,
        lookback_days=lookback_days,
        max_reports=max_reports,
    )


def update_weekly_report(
    weekly_flow_token: str,
    changes: list[dict[str, str]],
    subject: str | None = None,
) -> dict[str, Any]:
    """只能作为 get_weekly_report_context 之后的第二步调用。

    weekly_flow_token 必须是针对用户本次输入刚返回的有效 token；旧 token、
    重复调用、并发调用、过期 token 或被新上下文替代的 token 都会在任何
    Exchange 写入前被拒绝。changes 中每项只包含本次上下文中的 slot_id 和
    经周报化改写的 new_text。Server 自行取得原槽位文本，填回原模板并创建 Reply All 草稿。
    """
    return service.update_weekly_report(
        weekly_flow_token=weekly_flow_token,
        changes=changes,
        subject=subject,
    )


def forward_email(
    to_queries: list[str],
    body_html: str,
    cc_queries: list[str] | None = None,
    bcc_queries: list[str] | None = None,
    message_ref: str | None = None,
    folders: list[str] | None = None,
    sender_query: str | None = None,
    participant_query: str | None = None,
    subject_contains: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 20,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """解析目标收件人、定位原邮件并创建转发草稿，绝不发送。"""
    return service.forward_email(
        to_queries=to_queries,
        cc_queries=cc_queries,
        bcc_queries=bcc_queries,
        body_html=body_html,
        message_ref=message_ref,
        folders=folders,
        sender_query=sender_query,
        participant_query=participant_query,
        subject_contains=subject_contains,
        after=after,
        before=before,
        limit=limit,
        lookback_days=lookback_days,
    )


def continue_action(
    resume_token: str,
    selections: dict[str, str],
) -> dict[str, Any]:
    """用用户确认的 person_ref/email/message_ref 恢复被中断的语义邮件任务。"""
    return service.continue_action(resume_token=resume_token, selections=selections)


def update_email_draft(
    draft_ref: str,
    subject: str | None = None,
    body_html: str | None = None,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    importance: str | None = None,
) -> dict[str, Any]:
    """使用 draft_ref 修改语义工作流创建的草稿，绝不发送。"""
    return service.update_email_draft(
        draft_ref=draft_ref,
        subject=subject,
        body_html=body_html,
        to=to,
        cc=cc,
        bcc=bcc,
        importance=importance,
    )

def get_user_availability(
    attendees: list[dict[str, str]],
    start: str,
    end: str,
    interval_minutes: int = 30,
) -> dict[str, Any]:
    """查询一组邮箱在指定时间窗口内的忙闲事件和 EWS 工作时间。

    start/end 接受 ISO 8601；未带偏移时按 calendar_time_zone 解释。attendees 中每项包含 email，
    attendee_type 可为 Organizer/Required/Optional/Room/Resource。
    """
    return service.get_user_availability(
        attendees=attendees, start=start, end=end, interval_minutes=interval_minutes
    )


def list_calendar_events(
    start: str,
    end: str,
    limit: int = 100,
) -> dict[str, Any]:
    """列出当前用户日历事件；未带偏移的时间按 calendar_time_zone 解释。"""
    return service.list_calendar_events(start=start, end=end, limit=limit)


def get_calendar_item(
    item_id: str | None = None,
    calendar_ref: str | None = None,
    change_key: str | None = None,
) -> dict[str, Any]:
    """读取一个日历项目；item_id/calendar_ref 必须且只能提供一个。"""
    return service.get_calendar_item(
        item_id=item_id, calendar_ref=calendar_ref, change_key=change_key
    )


def create_meeting(
    subject: str,
    body_html: str,
    start: str,
    end: str,
    required_attendees: list[str],
    optional_attendees: list[str] | None = None,
    location: str | None = None,
    reminder_minutes: int = 15,
    send_invitations: bool = False,
    confirm_send: bool = False,
) -> dict[str, Any]:
    """使用已经确定的完整邮箱创建会议。

    start/end 未带偏移时按 calendar_time_zone 解释。默认 SendToNone，只保存到组织者日历而不发送邀请。只有显式设置
    send_invitations=true 且 confirm_send=true 才会向参会人发送会议邀请。
    """
    return service.create_meeting(
        subject=subject, body_html=body_html, start=start, end=end,
        required_attendees=required_attendees, optional_attendees=optional_attendees,
        location=location, reminder_minutes=reminder_minutes,
        send_invitations=send_invitations, confirm_send=confirm_send,
    )


def find_meeting_times(
    attendee_queries: list[str],
    window_start: str,
    window_end: str,
    duration_minutes: int = 60,
    interval_minutes: int | None = None,
    include_self: bool = True,
    max_results: int = 10,
    respect_attendee_working_hours: bool = True,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """解析参会人并查找共同空闲时间。

    人员查询只接受姓名拼音或完整邮箱。默认把当前用户作为 Organizer，
    同时尊重 Exchange 返回的个人工作时间；缺失时使用本地日历偏好。
    """
    return service.find_meeting_times(
        attendee_queries=attendee_queries, window_start=window_start, window_end=window_end,
        duration_minutes=duration_minutes, interval_minutes=interval_minutes,
        include_self=include_self, max_results=max_results,
        respect_attendee_working_hours=respect_attendee_working_hours,
        lookback_days=lookback_days,
    )


def schedule_meeting(
    attendee_queries: list[str],
    subject: str,
    body_html: str,
    start: str | None = None,
    end: str | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
    duration_minutes: int = 60,
    interval_minutes: int | None = None,
    location: str | None = None,
    optional_attendees: list[str] | None = None,
    include_self_in_availability: bool = True,
    respect_attendee_working_hours: bool = True,
    check_availability: bool = True,
    send_invitations: bool = False,
    confirm_send: bool = False,
    reminder_minutes: int = 15,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """解析参会人、选择共同空闲时间并创建会议。

    未提供 start/end 时使用 window_start/window_end 搜索候选时间。默认只保存
    未发送会议。send_invitations=true 时会先返回确认请求；只有 confirm_send=true
    或 continue_action 中明确 confirm=send 后才发送邀请。
    """
    return service.schedule_meeting(
        attendee_queries=attendee_queries, subject=subject, body_html=body_html,
        start=start, end=end, window_start=window_start, window_end=window_end,
        duration_minutes=duration_minutes, interval_minutes=interval_minutes,
        location=location, optional_attendees=optional_attendees,
        include_self_in_availability=include_self_in_availability,
        respect_attendee_working_hours=respect_attendee_working_hours,
        check_availability=check_availability, send_invitations=send_invitations,
        confirm_send=confirm_send, reminder_minutes=reminder_minutes,
        lookback_days=lookback_days,
    )




def create_mcp(*, include_debug_tools: bool = False) -> FastMCP:
    profile = "Debug" if include_debug_tools else "Production"
    instance = FastMCP(f"Exchange EWS Mail and Calendar Assistant ({profile})")
    namespace = globals()
    for name in tool_names(include_debug_tools=include_debug_tools):
        instance.tool()(namespace[name])
    return instance


mcp = create_mcp(include_debug_tools=False)
debug_mcp = create_mcp(include_debug_tools=True)


def main() -> None:
    """Start the production MCP server with the curated 19-tool surface."""
    mcp.run(transport="stdio")


def debug_main() -> None:
    """Start the full 23-tool MCP server for development and troubleshooting."""
    debug_mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
