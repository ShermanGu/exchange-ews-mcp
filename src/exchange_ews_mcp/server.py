from __future__ import annotations

from typing import Any, Literal

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
    max_reports: int = 3,
) -> dict[str, Any]:
    """内部兼容 helper；不注册为 Agent MCP tool。

    最多读取三周：最新一周返回为短 id 的 slots，前两周作为纯文本 history。
    HTML、内部 slot id、offset/hash、收件人等全部保留在 Server 的 resume_token
    上下文中；本 helper 只读，不创建 Exchange 草稿。
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


def weekly_report(
    user_input: str,
    reference_materials: list[dict[str, str]] | None = None,
    subject_contains: str = "周报",
    folder: str = "sentitems",
    folders: list[str] | None = None,
    lookback_days: int = 60,
) -> dict[str, Any]:
    """处理“周报”请求的唯一 Agent 入口。

    返回紧凑 JSON：resume_token、mode、subject、request、最新模板的 slots，
    以及前两周 history。slots.id 是本次上下文局部短 ID（s1、s2...），必须原样
    使用，不得猜测或自行构造；loc 只用于理解版面，不参与真正写入定位。

    生成下一周内容时：用户本次事实优先于历史；只提交确实需要变化的槽位；
    将口语化事实改写为简洁、正式、客观的周报文本；延续历史中的正式项目名和
    技术术语，不得虚构事实。必须检查正文中所有周期性日期/周次并顺延。Server
    会自动把可识别的 Subject 日期/周次顺延一周；返回的 subject 已是默认草稿主题。
    然后调用 continue_action(resume_token=..., selections={"changes":[{"id":"s1","text":"..."}]})。
    仅当用户明确要求不同主题时才传 selections.subject 覆盖默认值。本地参数错误时修正后复用同一 token；只有明确
    context_stale、token 过期/已使用/被取代时才重新调用 weekly_report。

    本工具不创建草稿，也绝不向 Agent 暴露 HTML。Server 会在 continue_action
    阶段自动决定 Reply All 或新建草稿。
    """
    return service.weekly_report(
        user_input=user_input,
        reference_materials=reference_materials,
        subject_contains=subject_contains,
        folder=folder,
        folders=folders,
        lookback_days=lookback_days,
    )


def update_weekly_report(
    weekly_flow_token: str,
    changes: list[dict[str, str]],
    subject: str | None = None,
) -> dict[str, Any]:
    """内部兼容 helper；不注册为 Agent MCP tool。

    Agent 第二步统一调用 continue_action。weekly_flow_token 必须是针对用户本次输入刚返回的有效 token；旧 token、
    重复调用、并发调用、过期 token 或被新上下文替代的 token 都会在任何
    Exchange 写入前被拒绝。changes 中每项只包含本次上下文中的短 id 和
    经周报化改写的 text。Server 自行取得原槽位文本，填回原模板，并按已记录的 draft_mode 创建 Reply All 或新建草稿。
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
    selections: dict[str, Any],
) -> dict[str, Any]:
    """恢复人员/邮件/时间确认，或提交 weekly_report 周报修改。

    周报 selections 仅支持 changes 和可选 subject；changes 每项使用 weekly_report
    返回的短 id/text，例如 {"id":"s3","text":"完成接口联调"}。weekly_report 返回的
    subject 已是 Server 自动顺延后的默认草稿主题；通常无需再次提交 subject。
    """
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
    """仅修改邮件草稿，绝不发送。

    draft_ref 必须是 draft_ 引用；绝不能传 calendar_ref/cal_ 引用。
    修改会议主题或邀请正文必须调用 update_meeting。
    """
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
    attendee_type 仅支持 Organizer/Required/Optional；不支持会议室或资源邮箱。
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


def update_meeting(
    item_id: str | None = None,
    calendar_ref: str | None = None,
    subject: str | None = None,
    body_html: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    required_attendees: list[str] | None = None,
    optional_attendees: list[str] | None = None,
    reminder_minutes: int | None = None,
) -> dict[str, Any]:
    """修改一个尚未发送邀请的会议，并继续仅保存到组织者日历。

    calendar_ref 应来自 schedule_meeting/create_meeting/get_calendar_item 返回的 cal_ 引用；
    不要调用 update_email_draft 修改会议。item_id/calendar_ref 必须且只能提供一个。
    start/end 必须成对提供；人员列表只接受完整邮箱。传入 location="" 可清空地点。
    该工具不会向参会人发送任何通知。
    """
    return service.update_meeting(
        item_id=item_id,
        calendar_ref=calendar_ref,
        subject=subject,
        body_html=body_html,
        start=start,
        end=end,
        location=location,
        required_attendees=required_attendees,
        optional_attendees=optional_attendees,
        reminder_minutes=reminder_minutes,
    )


def send_meeting_invitation(
    calendar_ref: str,
    confirm_send: bool = False,
) -> dict[str, Any]:
    """发送一个已保存但尚未发送的会议邀请。

    只有显式设置 confirm_send=true 才会发送；已发送或已取消的会议会被拒绝，
    避免重复邀请。必须使用 save_meeting/read_calendar 返回的 calendar_ref。
    """
    return service.send_meeting_invitation(
        calendar_ref=calendar_ref,
        confirm_send=confirm_send,
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


def search_mail(
    folders: list[str] | None = None,
    sender_query: str | None = None,
    participant_query: str | None = None,
    subject_contains: str | None = None,
    unread_only: bool | None = None,
    has_attachments: bool | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 20,
    offset: int = 0,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """统一查找邮件并返回类型安全的稳定引用。

    普通邮件返回 message_ref；草稿返回 draft_ref，不会同时伪装成可回复的消息。
    无过滤条件时列出 inbox/sentitems 中最近邮件；姓名拼音或完整邮箱由 Server
    解析和消歧。需要用户选择时返回 resume_token，由 continue_action 恢复。
    """
    return service.search_mail(
        folders=folders,
        sender_query=sender_query,
        participant_query=participant_query,
        subject_contains=subject_contains,
        unread_only=unread_only,
        has_attachments=has_attachments,
        after=after,
        before=before,
        limit=limit,
        offset=offset,
        lookback_days=lookback_days,
    )


def read_mail(
    message_ref: str | None = None,
    draft_ref: str | None = None,
    max_body_chars: int = 50000,
) -> dict[str, Any]:
    """读取邮件或草稿的正文、收发件人和附件元数据。

    message_ref 与 draft_ref 必须且只能提供一个；不接受底层 Exchange ItemId。
    """
    return service.read_mail(
        message_ref=message_ref,
        draft_ref=draft_ref,
        max_body_chars=max_body_chars,
    )


def save_mail_draft(
    mode: Literal["compose", "reply", "reply_all", "forward"],
    body_html: str,
    source_message_ref: str | None = None,
    to_queries: list[str] | None = None,
    cc_queries: list[str] | None = None,
    bcc_queries: list[str] | None = None,
    subject: str | None = None,
    attachments: list[str] | None = None,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """新建、回复、Reply All 或转发邮件草稿，绝不发送。

    compose 需要 subject/to_queries；其他模式需要 source_message_ref。reply 和
    reply_all 不接受收件人；forward 需要 to_queries。reply/forward 创建后如需
    改主题或加附件，继续调用 edit_mail_draft。
    """
    return service.save_mail_draft(
        mode=mode,
        body_html=body_html,
        source_message_ref=source_message_ref,
        to_queries=to_queries,
        cc_queries=cc_queries,
        bcc_queries=bcc_queries,
        subject=subject,
        attachments=attachments,
        lookback_days=lookback_days,
    )


def edit_mail_draft(
    draft_ref: str,
    subject: str | None = None,
    body_html: str | None = None,
    to: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    importance: str | None = None,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    """修改邮件草稿并可追加允许目录中的本地附件，绝不发送。

    所有附件会在第一次 Exchange 写入前完成路径和大小校验。draft_ref 只能是
    邮件草稿引用；会议必须使用 save_meeting。
    """
    return service.edit_mail_draft(
        draft_ref=draft_ref,
        subject=subject,
        body_html=body_html,
        to=to,
        cc=cc,
        bcc=bcc,
        importance=importance,
        attachments=attachments,
    )


def read_calendar(
    start: str | None = None,
    end: str | None = None,
    calendar_ref: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """按时间范围列出日程，或按 calendar_ref 读取一个日历项目。

    读取单项时只提供 calendar_ref；列出日程时同时提供 start/end。
    """
    return service.read_calendar(
        start=start,
        end=end,
        calendar_ref=calendar_ref,
        limit=limit,
    )


def save_meeting(
    calendar_ref: str | None = None,
    attendee_queries: list[str] | None = None,
    subject: str | None = None,
    body_html: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    optional_attendees: list[str] | None = None,
    check_availability: bool = True,
    reminder_minutes: int | None = None,
    lookback_days: int = 365,
) -> dict[str, Any]:
    """创建或修改会议并始终保持未发送状态。

    没有 calendar_ref 时创建：需要 attendee_queries、subject、body_html，以及
    确定的 start/end；如需查找候选时间，先调用 find_meeting_times。提供
    calendar_ref 时更新现有未发送会议；attendee_queries 此时只接受完整邮箱。
    发送邀请必须另行调用 send_meeting_invitation。
    """
    return service.save_meeting(
        calendar_ref=calendar_ref,
        attendee_queries=attendee_queries,
        subject=subject,
        body_html=body_html,
        start=start,
        end=end,
        location=location,
        optional_attendees=optional_attendees,
        check_availability=check_availability,
        reminder_minutes=reminder_minutes,
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
    """Start the production MCP server with the compact 11-tool surface."""
    mcp.run(transport="stdio")


def debug_main() -> None:
    """Start the 17-tool server for development and troubleshooting."""
    debug_mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
