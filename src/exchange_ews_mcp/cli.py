from __future__ import annotations

import argparse
import getpass
import json
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, service
from .calendar_utils import normalize_hhmm
from .config import AppConfig, config_path, effective_company_domains, load_config, save_config
from .credentials import delete_password, get_password, store_password
from .dt_config import DtTestConfig, delete_dt_config, dt_config_path, load_dt_config, save_dt_config
from .dt_runner import run_dt_suite
from .errors import ExchangeMcpError
from .ews import EwsClient
from .phase2_config import phase2_config_path
from .service import configured_client
from .state_store import default_state_path
from .workflow_test_config import phase023_config_path


def _split_values(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def _split_addresses(values: list[str] | None) -> list[str]:
    return _split_values(values)


def _normalize_domains(values: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in _split_values(values):
        value = raw.strip().casefold()
        if value.startswith("@"):
            value = value[1:]
        if not value or "@" in value or " " in value:
            raise ValueError(f"无效的公司邮箱域名：{raw!r}")
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _parse_workdays(values: list[str] | None) -> list[int]:
    mapping = {
        "monday": 0, "mon": 0, "周一": 0, "星期一": 0,
        "tuesday": 1, "tue": 1, "周二": 1, "星期二": 1,
        "wednesday": 2, "wed": 2, "周三": 2, "星期三": 2,
        "thursday": 3, "thu": 3, "周四": 3, "星期四": 3,
        "friday": 4, "fri": 4, "周五": 4, "星期五": 4,
        "saturday": 5, "sat": 5, "周六": 5, "星期六": 5,
        "sunday": 6, "sun": 6, "周日": 6, "星期日": 6,
    }
    result: list[int] = []
    for raw in _split_values(values):
        key = raw.strip().casefold()
        try:
            day = int(key) if key.isdigit() else mapping[key]
        except (ValueError, KeyError) as exc:
            raise ValueError(f"无效工作日：{raw!r}。使用 0..6 或 Monday..Sunday。") from exc
        if not 0 <= day <= 6:
            raise ValueError("工作日数字必须在 0..6，Monday=0。")
        if day not in result:
            result.append(day)
    if not result:
        raise ValueError("至少需要一个工作日。")
    return sorted(result)


def _body_from_args(args: argparse.Namespace) -> str:
    if getattr(args, "html_file", None):
        return Path(args.html_file).read_text(encoding="utf-8")
    body_html = getattr(args, "html", None)
    if body_html is None:
        raise ValueError("请使用 --html 或 --html-file 提供 HTML 正文。")
    return body_html


def _optional_body_from_args(args: argparse.Namespace) -> str | None:
    if getattr(args, "html_file", None):
        return Path(args.html_file).read_text(encoding="utf-8")
    return getattr(args, "html", None)


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError):
            pass


def version_command(args: argparse.Namespace) -> int:
    _print_json(
        {
            "name": "exchange-ews-mcp",
            "version": __version__,
            "package_path": str(Path(__file__).resolve().parent),
            "python": sys.executable,
        }
    )
    return 0


def configure(args: argparse.Namespace) -> int:
    try:
        current = load_config()
    except ExchangeMcpError:
        current = None

    default_url = current.ews_url if current else ""
    default_username = current.username if current else ""
    ews_url = input(f"EWS URL [{default_url}]: ").strip() or default_url
    username = input(f"NTLM 用户名 [{default_username}]: ").strip() or default_username
    default_domains = ",".join(effective_company_domains(current)) if current else ""
    domain_input = input(
        f"公司邮箱域名（如 company.com，可逗号分隔） [{default_domains}]: "
    ).strip()
    company_domains = _normalize_domains([domain_input] if domain_input else [default_domains])
    if not company_domains and current and current.primary_email and "@" in current.primary_email:
        company_domains = [current.primary_email.rsplit("@", 1)[1].casefold()]
    if not company_domains:
        raise ValueError("需要至少配置一个公司邮箱域名，例如 company.com。")
    verify_input = input("验证 TLS 证书？[Y/n]: ").strip().lower()
    verify_tls = verify_input not in {"n", "no"}
    default_time_zone = current.calendar_time_zone if current else "UTC"
    calendar_time_zone = input(
        f"日历 IANA 时区（如 Asia/Shanghai） [{default_time_zone}]: "
    ).strip() or default_time_zone
    default_workday_start = current.calendar_workday_start if current else "09:00"
    default_workday_end = current.calendar_workday_end if current else "18:00"
    calendar_workday_start = normalize_hhmm(
        input(f"工作日开始时间 [{default_workday_start}]: ").strip()
        or default_workday_start,
        "calendar_workday_start",
    )
    calendar_workday_end = normalize_hhmm(
        input(f"工作日结束时间 [{default_workday_end}]: ").strip()
        or default_workday_end,
        "calendar_workday_end",
    )
    default_workdays = current.calendar_workdays if current and current.calendar_workdays else [0, 1, 2, 3, 4]
    workday_text = input(
        f"工作日（0=周一，逗号分隔） [{','.join(str(v) for v in default_workdays)}]: "
    ).strip()
    calendar_workdays = _parse_workdays([workday_text]) if workday_text else default_workdays
    default_interval = current.calendar_slot_interval_minutes if current else 30
    interval_text = input(f"会议候选时间粒度（分钟） [{default_interval}]: ").strip()
    calendar_slot_interval_minutes = int(interval_text) if interval_text else default_interval
    password = getpass.getpass("公司邮箱密码（不会显示）: ")

    config = AppConfig(
        ews_url=ews_url,
        username=username,
        verify_tls=verify_tls,
        timeout_seconds=args.timeout,
        exchange_version=args.exchange_version,
        attachment_roots=current.attachment_roots if current else None,
        max_attachment_bytes=current.max_attachment_bytes if current else 10 * 1024 * 1024,
        primary_email=current.primary_email if current else None,
        display_name=current.display_name if current else None,
        company_email_domains=company_domains,
        calendar_time_zone=calendar_time_zone,
        calendar_workday_start=calendar_workday_start,
        calendar_workday_end=calendar_workday_end,
        calendar_workdays=calendar_workdays,
        calendar_slot_interval_minutes=calendar_slot_interval_minutes,
    )
    config.validate()

    if not args.skip_test:
        inbox_name = EwsClient(config, password).test_connection()
        print(f"认证成功，已访问邮箱文件夹：{inbox_name}")

    store_password(username, password)
    path = save_config(config)
    print(f"配置已保存：{path}")
    print("密码已保存到系统凭据库（Windows 上为 Credential Manager）。")
    return 0


def set_current_user(args: argparse.Namespace) -> int:
    config = load_config()
    email = args.email.strip()
    inferred_domain = email.rsplit("@", 1)[1].casefold() if "@" in email else None
    domains = _normalize_domains(args.company_domain) if args.company_domain else list(config.company_email_domains or [])
    if inferred_domain and inferred_domain not in domains:
        domains.append(inferred_domain)
    updated = replace(
        config,
        primary_email=email,
        display_name=args.display_name.strip() if args.display_name else None,
        company_email_domains=domains,
    )
    path = save_config(updated)
    _print_json(
        {
            "status": "updated",
            "config_path": str(path),
            "primary_email": updated.primary_email,
            "display_name": updated.display_name,
            "company_email_domains": updated.company_email_domains,
        }
    )
    return 0


def status(_: argparse.Namespace) -> int:
    config = load_config()
    get_password(config.username)
    _print_json(
        {
            "configured": True,
            "config_path": str(config_path()),
            "ews_url": config.ews_url,
            "username": config.username,
            "verify_tls": config.verify_tls,
            "primary_email": config.primary_email,
            "display_name": config.display_name,
            "company_email_domains": effective_company_domains(config),
            "attachment_roots": config.attachment_roots,
            "default_attachment_roots": [
                str(Path.home() / "Desktop"),
                str(Path.home() / "Documents"),
                str(Path.home() / "Downloads"),
            ],
            "max_attachment_bytes": config.max_attachment_bytes,
            "calendar_preferences": {
                "time_zone": config.calendar_time_zone,
                "workday_start": config.calendar_workday_start,
                "workday_end": config.calendar_workday_end,
                "workdays": config.calendar_workdays or [0, 1, 2, 3, 4],
                "slot_interval_minutes": config.calendar_slot_interval_minutes,
            },
            "credential": "available-in-system-keyring",
        }
    )
    return 0


def test_connection(_: argparse.Namespace) -> int:
    inbox_name = configured_client().test_connection()
    print(f"EWS 连接成功，收件箱显示名：{inbox_name}")
    return 0


def current_user_command(_: argparse.Namespace) -> int:
    _print_json(service.get_current_user())
    return 0


def resolve_names_command(args: argparse.Namespace) -> int:
    _print_json(
        service.resolve_names(query=args.query, limit=args.limit)
    )
    return 0


def draft(args: argparse.Namespace) -> int:
    _print_json(
        service.create_draft(
            to=_split_addresses(args.to),
            cc=_split_addresses(args.cc),
            bcc=_split_addresses(args.bcc),
            subject=args.subject,
            body_html=_body_from_args(args),
        )
    )
    return 0


def list_command(args: argparse.Namespace) -> int:
    _print_json(
        service.list_emails(
            folder=args.folder,
            limit=args.limit,
            offset=args.offset,
            unread_only=args.unread_only,
        )
    )
    return 0


def search_command(args: argparse.Namespace) -> int:
    _print_json(
        service.search_emails(
            folder=args.folder,
            folders=_split_values(args.folders) or None,
            subject_contains=args.subject_contains,
            sender=args.sender,
            to_contains=args.to_contains,
            cc_contains=args.cc_contains,
            participant_contains=args.participant_contains,
            unread_only=args.unread_only,
            has_attachments=args.has_attachments,
            conversation_id=args.conversation_id,
            internet_message_id=args.internet_message_id,
            after=args.after,
            before=args.before,
            limit=args.limit,
            offset=args.offset,
        )
    )
    return 0


def get_command(args: argparse.Namespace) -> int:
    _print_json(
        service.get_email(
            item_id=args.item_id,
            message_ref=args.message_ref,
            draft_ref=args.draft_ref,
            change_key=args.change_key,
            max_body_chars=args.max_body_chars,
        )
    )
    return 0


def reply_command(args: argparse.Namespace) -> int:
    _print_json(
        service.reply_as_draft(
            item_id=args.item_id,
            message_ref=args.message_ref,
            change_key=args.change_key,
            body_html=_body_from_args(args),
            reply_all=args.reply_all,
        )
    )
    return 0


def forward_command(args: argparse.Namespace) -> int:
    _print_json(
        service.forward_as_draft(
            item_id=args.item_id,
            message_ref=args.message_ref,
            change_key=args.change_key,
            to=_split_addresses(args.to),
            cc=_split_addresses(args.cc),
            bcc=_split_addresses(args.bcc),
            body_html=_body_from_args(args),
        )
    )
    return 0


def update_draft_command(args: argparse.Namespace) -> int:
    _print_json(
        service.update_draft(
            item_id=args.item_id,
            draft_ref=args.draft_ref,
            change_key=args.change_key,
            subject=args.subject,
            body_html=_optional_body_from_args(args),
            to=_split_addresses(args.to) if args.to is not None else None,
            cc=_split_addresses(args.cc) if args.cc is not None else None,
            bcc=_split_addresses(args.bcc) if args.bcc is not None else None,
            importance=args.importance,
        )
    )
    return 0


def attach_command(args: argparse.Namespace) -> int:
    _print_json(
        service.add_attachment_to_draft(
            item_id=args.item_id,
            draft_ref=args.draft_ref,
            change_key=args.change_key,
            file_path=args.file,
            attachment_name=args.name,
        )
    )
    return 0


def set_attachment_roots(args: argparse.Namespace) -> int:
    config = load_config()
    roots = [str(Path(root).expanduser().resolve()) for root in args.root]
    updated = replace(
        config,
        attachment_roots=roots,
        max_attachment_bytes=args.max_bytes or config.max_attachment_bytes,
    )
    path = save_config(updated)
    _print_json(
        {
            "status": "updated",
            "config_path": str(path),
            "attachment_roots": roots,
            "max_attachment_bytes": updated.max_attachment_bytes,
        }
    )
    return 0


def set_company_domains(args: argparse.Namespace) -> int:
    config = load_config()
    domains = _normalize_domains(args.domain)
    updated = replace(config, company_email_domains=domains)
    path = save_config(updated)
    _print_json({"status": "updated", "config_path": str(path), "company_email_domains": domains})
    return 0


def resolve_people_command(args: argparse.Namespace) -> int:
    _print_json(service.resolve_people(
        query=args.query, limit=args.limit, lookback_days=args.lookback_days,
        auto_select=not args.no_auto_select,
    ))
    return 0


def compose_email_command(args: argparse.Namespace) -> int:
    _print_json(service.compose_email(
        to_queries=_split_values(args.to),
        cc_queries=_split_values(args.cc),
        bcc_queries=_split_values(args.bcc),
        subject=args.subject,
        body_html=_body_from_args(args),
        attachments=_split_values(args.attachment),
        lookback_days=args.lookback_days,
    ))
    return 0


def find_email_command(args: argparse.Namespace) -> int:
    _print_json(service.find_email(
        folders=_split_values(args.folders) or None,
        sender_query=args.sender_query,
        participant_query=args.participant_query,
        subject_contains=args.subject_contains,
        after=args.after, before=args.before, limit=args.limit,
        lookback_days=args.lookback_days,
    ))
    return 0


def reply_email_command(args: argparse.Namespace) -> int:
    _print_json(service.reply_to_email(
        body_html=_body_from_args(args), reply_all=args.reply_all,
        message_ref=args.message_ref, folders=_split_values(args.folders) or None,
        sender_query=args.sender_query, participant_query=args.participant_query,
        subject_contains=args.subject_contains, after=args.after, before=args.before,
        limit=args.limit, lookback_days=args.lookback_days,
    ))
    return 0


def forward_email_command(args: argparse.Namespace) -> int:
    _print_json(service.forward_email(
        to_queries=_split_values(args.to), cc_queries=_split_values(args.cc),
        bcc_queries=_split_values(args.bcc), body_html=_body_from_args(args),
        message_ref=args.message_ref, folders=_split_values(args.folders) or None,
        sender_query=args.sender_query, participant_query=args.participant_query,
        subject_contains=args.subject_contains, after=args.after, before=args.before,
        limit=args.limit, lookback_days=args.lookback_days,
    ))
    return 0


def continue_action_command(args: argparse.Namespace) -> int:
    selections: dict[str, str] = {}
    for raw in args.select or []:
        if "=" not in raw:
            raise ValueError("--select 必须使用 key=value，例如 --select xiaoming=person_xxx。")
        key, value = raw.split("=", 1)
        selections[key.strip()] = value.strip()
    _print_json(service.continue_action(resume_token=args.resume_token, selections=selections))
    return 0


def set_calendar_preferences(args: argparse.Namespace) -> int:
    config = load_config()
    workdays = _parse_workdays(args.workday) if args.workday else (config.calendar_workdays or [0, 1, 2, 3, 4])
    updated = replace(
        config,
        calendar_time_zone=args.time_zone or config.calendar_time_zone,
        calendar_workday_start=normalize_hhmm(
            args.workday_start or config.calendar_workday_start,
            "calendar_workday_start",
        ),
        calendar_workday_end=normalize_hhmm(
            args.workday_end or config.calendar_workday_end,
            "calendar_workday_end",
        ),
        calendar_workdays=workdays,
        calendar_slot_interval_minutes=(args.slot_interval_minutes or config.calendar_slot_interval_minutes),
    )
    path = save_config(updated)
    _print_json({
        "status": "updated", "config_path": str(path),
        "calendar_time_zone": updated.calendar_time_zone,
        "calendar_workday_start": updated.calendar_workday_start,
        "calendar_workday_end": updated.calendar_workday_end,
        "calendar_workdays": updated.calendar_workdays,
        "calendar_slot_interval_minutes": updated.calendar_slot_interval_minutes,
    })
    return 0


def availability_command(args: argparse.Namespace) -> int:
    attendees: list[dict[str, str]] = []
    for raw in args.attendee:
        value = raw.strip()
        if ":" in value and "@" in value.split(":", 1)[0]:
            email, attendee_type = value.rsplit(":", 1)
        else:
            email, attendee_type = value, "Required"
        attendees.append({"email": email.strip(), "attendee_type": attendee_type.strip()})
    _print_json(service.get_user_availability(
        attendees=attendees, start=args.start, end=args.end,
        interval_minutes=args.interval_minutes,
    ))
    return 0


def calendar_list_command(args: argparse.Namespace) -> int:
    _print_json(service.list_calendar_events(start=args.start, end=args.end, limit=args.limit))
    return 0


def calendar_get_command(args: argparse.Namespace) -> int:
    _print_json(service.get_calendar_item(
        item_id=args.item_id, calendar_ref=args.calendar_ref, change_key=args.change_key
    ))
    return 0


def update_meeting_command(args: argparse.Namespace) -> int:
    _print_json(service.update_meeting(
        item_id=args.item_id,
        calendar_ref=args.calendar_ref,
        subject=args.subject,
        body_html=_optional_body_from_args(args),
        start=args.start,
        end=args.end,
        location=args.location,
        required_attendees=(
            _split_addresses(args.required_attendee)
            if args.required_attendee is not None
            else None
        ),
        optional_attendees=(
            _split_addresses(args.optional_attendee)
            if args.optional_attendee is not None
            else None
        ),
        reminder_minutes=args.reminder_minutes,
    ))
    return 0


def send_meeting_invitation_command(args: argparse.Namespace) -> int:
    _print_json(service.send_meeting_invitation(
        item_id=args.item_id,
        calendar_ref=args.calendar_ref,
        confirm_send=args.confirm_send,
    ))
    return 0


def create_meeting_command(args: argparse.Namespace) -> int:
    _print_json(service.create_meeting(
        subject=args.subject, body_html=_body_from_args(args),
        start=args.start, end=args.end,
        required_attendees=_split_addresses(args.required_attendee),
        optional_attendees=_split_addresses(args.optional_attendee),
        location=args.location, reminder_minutes=args.reminder_minutes,
        send_invitations=args.send_invitations, confirm_send=args.confirm_send,
    ))
    return 0


def find_meeting_times_command(args: argparse.Namespace) -> int:
    _print_json(service.find_meeting_times(
        attendee_queries=_split_values(args.attendee),
        window_start=args.window_start, window_end=args.window_end,
        duration_minutes=args.duration_minutes, interval_minutes=args.interval_minutes,
        include_self=not args.exclude_self, max_results=args.max_results,
        respect_attendee_working_hours=not args.ignore_attendee_working_hours,
        lookback_days=args.lookback_days,
    ))
    return 0


def schedule_meeting_command(args: argparse.Namespace) -> int:
    _print_json(service.schedule_meeting(
        attendee_queries=_split_values(args.attendee),
        subject=args.subject, body_html=_body_from_args(args),
        start=args.start, end=args.end, window_start=args.window_start,
        window_end=args.window_end, duration_minutes=args.duration_minutes,
        interval_minutes=args.interval_minutes, location=args.location,
        optional_attendees=_split_values(args.optional_attendee),
        include_self_in_availability=not args.exclude_self,
        respect_attendee_working_hours=not args.ignore_attendee_working_hours,
        check_availability=not args.skip_availability_check,
        send_invitations=args.send_invitations, confirm_send=args.confirm_send,
        reminder_minutes=args.reminder_minutes, lookback_days=args.lookback_days,
    ))
    return 0


def configure_dt(args: argparse.Namespace) -> int:
    config = DtTestConfig(
        person_queries=_split_values(args.person_query),
        senders=_split_addresses(args.sender),
        draft_recipient=args.draft_recipient,
        subject_contains=args.subject_contains,
        search_limit=args.search_limit,
    )
    path = save_dt_config(config)
    normalized = load_dt_config()
    _print_json(
        {
            "status": "configured",
            "config_path": str(path),
            "person_queries": normalized.person_queries,
            "senders": normalized.senders,
            "draft_recipient": normalized.draft_recipient,
            "subject_contains": normalized.subject_contains,
            "search_limit": normalized.search_limit,
            "groups": ["atomic", "workflow-v03", "semantic-mail-v04", "calendar-v05", "weekly-report-v06"],
        }
    )
    return 0


def show_dt_config(_: argparse.Namespace) -> int:
    config = load_dt_config()
    _print_json(
        {
            "configured": True,
            "config_path": str(dt_config_path()),
            **config.__dict__,
            "groups": ["atomic", "workflow-v03", "semantic-mail-v04", "calendar-v05", "weekly-report-v06"],
        }
    )
    return 0


def clear_dt_config(_: argparse.Namespace) -> int:
    removed = delete_dt_config()
    print("已删除统一 DT 配置。" if removed else "统一 DT 配置不存在。")
    return 0


def dt_test(args: argparse.Namespace) -> int:
    report = run_dt_suite(
        configured_client(),
        load_dt_config(),
        read_only=args.read_only,
        groups=args.group,
        app_config=load_config(),
    )
    return _print_test_report(report)

def _print_test_report(report: dict[str, object]) -> int:
    groups = report.get("groups") or []
    if groups:
        for group in groups:
            print(f"\n=== {group['label']} ({group['id']}) ===")
            for step in group["steps"]:
                label = step["status"]
                suffix = ""
                if label == "FAIL":
                    suffix = f": {step.get('error', '')}"
                elif label == "SKIP":
                    suffix = f": {step.get('details', '')}"
                print(f"[{label}] {step['name']}{suffix}")
            group_summary = group["summary"]
            print(
                f"Group Summary: {group_summary['status']} | "
                f"PASS={group_summary['passed']} FAIL={group_summary['failed']} "
                f"SKIP={group_summary['skipped']}"
            )
    else:
        for step in report["steps"]:  # type: ignore[index]
            label = step["status"]
            suffix = ""
            if label == "FAIL":
                suffix = f": {step.get('error', '')}"
            elif label == "SKIP":
                suffix = f": {step.get('details', '')}"
            print(f"[{label}] {step['name']}{suffix}")
    summary = report["summary"]  # type: ignore[index]
    print(
        f"Summary: {summary['status']} | PASS={summary['passed']} "
        f"FAIL={summary['failed']} SKIP={summary['skipped']}"
    )
    print(f"Report: {report['report_path']}")
    created = report.get("created_drafts") or []
    if created:
        print("Created drafts (not sent):")
        for draft_item in created:
            print(
                f"- {draft_item.get('draft_type')}: "
                f"{draft_item.get('draft_ref') or draft_item.get('item_id')}"
            )
    return 0 if summary["failed"] == 0 else 2


def reset_local(_: argparse.Namespace) -> int:
    removed: list[str] = []
    try:
        current = load_config()
    except ExchangeMcpError:
        current = None
    if current is not None:
        try:
            delete_password(current.username)
            removed.append("system credential")
        except Exception:
            pass
    for path in (
        config_path(),
        dt_config_path(),
        phase2_config_path(),
        phase023_config_path(),
        default_state_path(),
    ):
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except OSError as exc:
            raise OSError(f"无法删除 {path}: {exc}") from exc
    _print_json({"status": "reset", "removed": removed})
    return 0


def logout(_: argparse.Namespace) -> int:
    config = load_config()
    delete_password(config.username)
    print("已删除系统凭据库中的密码；非敏感配置文件仍保留。")
    return 0


def tool_list(args: argparse.Namespace) -> int:
    from .tool_profiles import DEBUG_ONLY_TOOL_NAMES, PRODUCTION_TOOL_NAMES

    production = list(PRODUCTION_TOOL_NAMES)
    debug_only = list(DEBUG_ONLY_TOOL_NAMES)
    if args.profile == "production":
        visible = production
    else:
        visible = production + debug_only
    _print_json(
        {
            "profile": args.profile,
            "visible_tool_count": len(visible),
            "visible_tools": visible,
            "production_tools": production,
            "debug_only_tools": debug_only,
            "recommendation": (
                "Agent 日常使用 production；仅在协议排查或原子能力调试时使用 debug。"
            ),
        }
    )
    return 0


def mcp_config(args: argparse.Namespace) -> int:
    command = str(Path(sys.executable).resolve())
    module = "exchange_ews_mcp.debug_server" if args.debug_tools else "exchange_ews_mcp.server"
    server_name = "exchange-ews-debug" if args.debug_tools else "exchange-ews"
    _print_json(
        {
            "mcpServers": {
                server_name: {
                    "command": command,
                    "args": ["-m", module],
                }
            },
            "tool_profile": "debug" if args.debug_tools else "production",
        }
    )
    return 0


def serve(args: argparse.Namespace) -> int:
    if args.debug_tools:
        from .server import debug_main as server_main
    else:
        from .server import main as server_main

    server_main()
    return 0


def _add_html_body(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    body = parser.add_mutually_exclusive_group(required=required)
    body.add_argument("--html")
    body.add_argument("--html-file")


def _add_paging(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--folder", default="inbox")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    read_group = parser.add_mutually_exclusive_group()
    read_group.add_argument("--unread-only", action="store_true", dest="unread_only")
    read_group.add_argument("--read-only", action="store_false", dest="unread_only")
    parser.set_defaults(unread_only=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exchange-ews-mcp",
        description="通过 EWS + NTLM 处理 Exchange 邮件、忙闲查询和会议协调。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("configure", help="配置 EWS 并把密码保存到系统凭据库")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--exchange-version", default="Exchange2010_SP2")
    p.add_argument("--skip-test", action="store_true")
    p.set_defaults(func=configure)

    p = sub.add_parser("set-current-user", help="显式保存当前用户的主 SMTP 邮箱")
    p.add_argument("--email", required=True)
    p.add_argument("--display-name")
    p.add_argument("--company-domain", action="append", help="可重复；默认自动加入当前邮箱域名")
    p.set_defaults(func=set_current_user)

    p = sub.add_parser("status", help="检查本地配置和凭据是否存在")
    p.set_defaults(func=status)

    p = sub.add_parser("test", help="使用已保存凭据测试 EWS")
    p.set_defaults(func=test_connection)

    p = sub.add_parser("current-user", help="解析当前认证用户")
    p.set_defaults(func=current_user_command)

    p = sub.add_parser(
        "resolve-names",
        help="使用姓名拼音或完整邮箱解析联系人",
    )
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=resolve_names_command)

    p = sub.add_parser("draft", help="手动创建一封新草稿")
    p.add_argument("--to", action="append", required=True)
    p.add_argument("--cc", action="append")
    p.add_argument("--bcc", action="append")
    p.add_argument("--subject", required=True)
    _add_html_body(p)
    p.set_defaults(func=draft)

    p = sub.add_parser("list", help="列出邮件摘要")
    _add_paging(p)
    p.set_defaults(func=list_command)

    p = sub.add_parser("search", help="增强邮件搜索")
    _add_paging(p)
    p.add_argument("--folders", action="append", help="跨文件夹搜索；可重复或逗号分隔")
    p.add_argument("--subject-contains")
    p.add_argument("--sender")
    p.add_argument("--to-contains")
    p.add_argument("--cc-contains")
    p.add_argument("--participant-contains")
    attachment_group = p.add_mutually_exclusive_group()
    attachment_group.add_argument("--has-attachments", action="store_true", dest="has_attachments")
    attachment_group.add_argument("--no-attachments", action="store_false", dest="has_attachments")
    p.set_defaults(has_attachments=None)
    p.add_argument("--conversation-id")
    p.add_argument("--internet-message-id")
    p.add_argument("--after")
    p.add_argument("--before")
    p.set_defaults(func=search_command)

    p = sub.add_parser("get", help="读取一封邮件的详情")
    identity = p.add_mutually_exclusive_group(required=True)
    identity.add_argument("--item-id")
    identity.add_argument("--message-ref")
    identity.add_argument("--draft-ref")
    p.add_argument("--change-key")
    p.add_argument("--max-body-chars", type=int, default=50000)
    p.set_defaults(func=get_command)

    p = sub.add_parser("reply-draft", help="创建回复或全部回复草稿")
    identity = p.add_mutually_exclusive_group(required=True)
    identity.add_argument("--item-id")
    identity.add_argument("--message-ref")
    p.add_argument("--change-key")
    p.add_argument("--reply-all", action="store_true")
    _add_html_body(p)
    p.set_defaults(func=reply_command)

    p = sub.add_parser("forward-draft", help="创建转发草稿")
    identity = p.add_mutually_exclusive_group(required=True)
    identity.add_argument("--item-id")
    identity.add_argument("--message-ref")
    p.add_argument("--change-key")
    p.add_argument("--to", action="append", required=True)
    p.add_argument("--cc", action="append")
    p.add_argument("--bcc", action="append")
    _add_html_body(p)
    p.set_defaults(func=forward_command)

    p = sub.add_parser("update-draft", help="修改已有草稿但不发送")
    identity = p.add_mutually_exclusive_group(required=True)
    identity.add_argument("--item-id")
    identity.add_argument("--draft-ref")
    p.add_argument("--change-key")
    p.add_argument("--subject")
    p.add_argument("--to", action="append")
    p.add_argument("--cc", action="append")
    p.add_argument("--bcc", action="append")
    p.add_argument("--importance", choices=["Low", "Normal", "High", "low", "normal", "high"])
    _add_html_body(p, required=False)
    p.set_defaults(func=update_draft_command)

    p = sub.add_parser("attach", help="给草稿添加本地附件")
    identity = p.add_mutually_exclusive_group(required=True)
    identity.add_argument("--item-id")
    identity.add_argument("--draft-ref")
    p.add_argument("--change-key")
    p.add_argument("--file", required=True)
    p.add_argument("--name")
    p.set_defaults(func=attach_command)

    p = sub.add_parser("set-attachment-roots", help="设置 Agent 允许读取附件的目录")
    p.add_argument("--root", action="append", required=True)
    p.add_argument("--max-bytes", type=int)
    p.set_defaults(func=set_attachment_roots)

    p = sub.add_parser("set-company-domains", help="设置一个或多个公司内部邮箱域名")
    p.add_argument("--domain", action="append", required=True)
    p.set_defaults(func=set_company_domains)

    p = sub.add_parser("resolve-people", help="结合邮件历史解析并消歧人员")
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--lookback-days", type=int, default=365)
    p.add_argument("--no-auto-select", action="store_true")
    p.set_defaults(func=resolve_people_command)

    p = sub.add_parser("compose-email", help="解析收件人并创建新邮件草稿")
    p.add_argument("--to", action="append", required=True)
    p.add_argument("--cc", action="append")
    p.add_argument("--bcc", action="append")
    p.add_argument("--subject", required=True)
    p.add_argument("--attachment", action="append")
    p.add_argument("--lookback-days", type=int, default=365)
    _add_html_body(p)
    p.set_defaults(func=compose_email_command)

    p = sub.add_parser("find-email", help="使用语义条件定位邮件")
    p.add_argument("--folders", action="append")
    p.add_argument("--sender-query")
    p.add_argument("--participant-query")
    p.add_argument("--subject-contains")
    p.add_argument("--after")
    p.add_argument("--before")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--lookback-days", type=int, default=365)
    p.set_defaults(func=find_email_command)

    p = sub.add_parser("reply-email", help="定位原邮件并创建回复草稿")
    p.add_argument("--message-ref")
    p.add_argument("--folders", action="append")
    p.add_argument("--sender-query")
    p.add_argument("--participant-query")
    p.add_argument("--subject-contains")
    p.add_argument("--after")
    p.add_argument("--before")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--lookback-days", type=int, default=365)
    p.add_argument("--reply-all", action="store_true")
    _add_html_body(p)
    p.set_defaults(func=reply_email_command)

    p = sub.add_parser("forward-email", help="定位原邮件、解析收件人并创建转发草稿")
    p.add_argument("--to", action="append", required=True)
    p.add_argument("--cc", action="append")
    p.add_argument("--bcc", action="append")
    p.add_argument("--message-ref")
    p.add_argument("--folders", action="append")
    p.add_argument("--sender-query")
    p.add_argument("--participant-query")
    p.add_argument("--subject-contains")
    p.add_argument("--after")
    p.add_argument("--before")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--lookback-days", type=int, default=365)
    _add_html_body(p)
    p.set_defaults(func=forward_email_command)

    p = sub.add_parser("continue-action", help="使用用户选择恢复语义邮件任务")
    p.add_argument("--resume-token", required=True)
    p.add_argument("--select", action="append", help="key=value，可重复")
    p.set_defaults(func=continue_action_command)

    p = sub.add_parser("set-calendar-preferences", help="设置时区、工作时间和会议时间粒度")
    p.add_argument("--time-zone", help="IANA 时区，例如 Asia/Shanghai")
    p.add_argument("--workday-start", help="H:MM 或 HH:MM，例如 9:30 或 09:30")
    p.add_argument("--workday-end", help="H:MM 或 HH:MM，例如 18:00")
    p.add_argument("--workday", action="append", help="0..6 或 Monday..Sunday；可重复/逗号分隔")
    p.add_argument("--slot-interval-minutes", type=int)
    p.set_defaults(func=set_calendar_preferences)

    p = sub.add_parser("availability", help="使用完整邮箱查询 EWS 忙闲信息")
    p.add_argument("--attendee", action="append", required=True, help="email 或 email:AttendeeType")
    p.add_argument("--start", required=True, help="ISO 8601；无偏移时按 calendar_time_zone 解释")
    p.add_argument("--end", required=True, help="ISO 8601；无偏移时按 calendar_time_zone 解释")
    p.add_argument("--interval-minutes", type=int, default=30)
    p.set_defaults(func=availability_command)

    p = sub.add_parser("calendar-list", help="列出时间窗口内的日历事件")
    p.add_argument("--start", required=True, help="ISO 8601；无偏移时按 calendar_time_zone 解释")
    p.add_argument("--end", required=True, help="ISO 8601；无偏移时按 calendar_time_zone 解释")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=calendar_list_command)

    p = sub.add_parser("calendar-get", help="读取一个日历项目")
    identity = p.add_mutually_exclusive_group(required=True)
    identity.add_argument("--item-id")
    identity.add_argument("--calendar-ref")
    p.add_argument("--change-key")
    p.set_defaults(func=calendar_get_command)

    p = sub.add_parser("update-meeting", help="修改一个尚未发送邀请的会议")
    identity = p.add_mutually_exclusive_group(required=True)
    identity.add_argument("--item-id")
    identity.add_argument("--calendar-ref")
    p.add_argument("--subject")
    p.add_argument("--start", help="必须与 --end 同时提供")
    p.add_argument("--end", help="必须与 --start 同时提供")
    p.add_argument("--location", help="传空字符串可清空地点")
    p.add_argument("--required-attendee", action="append")
    p.add_argument("--optional-attendee", action="append")
    p.add_argument("--reminder-minutes", type=int)
    _add_html_body(p, required=False)
    p.set_defaults(func=update_meeting_command)

    p = sub.add_parser("send-meeting-invitation", help="发送一个已保存但尚未发送的会议邀请")
    identity = p.add_mutually_exclusive_group(required=True)
    identity.add_argument("--item-id")
    identity.add_argument("--calendar-ref")
    p.add_argument("--confirm-send", action="store_true", required=True)
    p.set_defaults(func=send_meeting_invitation_command)

    p = sub.add_parser("create-meeting", help="使用完整邮箱创建会议；默认不发送邀请")
    p.add_argument("--subject", required=True)
    p.add_argument("--start", required=True, help="ISO 8601；无偏移时按 calendar_time_zone 解释")
    p.add_argument("--end", required=True, help="ISO 8601；无偏移时按 calendar_time_zone 解释")
    p.add_argument("--required-attendee", action="append", required=True)
    p.add_argument("--optional-attendee", action="append")
    p.add_argument("--location")
    p.add_argument("--reminder-minutes", type=int, default=15)
    p.add_argument("--send-invitations", action="store_true")
    p.add_argument("--confirm-send", action="store_true", help="显式确认发送会议邀请")
    _add_html_body(p)
    p.set_defaults(func=create_meeting_command)

    p = sub.add_parser("find-meeting-times", help="解析参会人并查找共同空闲时间")
    p.add_argument("--attendee", action="append", required=True)
    p.add_argument("--window-start", required=True, help="ISO 8601；无偏移时按 calendar_time_zone 解释")
    p.add_argument("--window-end", required=True, help="ISO 8601；无偏移时按 calendar_time_zone 解释")
    p.add_argument("--duration-minutes", type=int, default=60)
    p.add_argument("--interval-minutes", type=int)
    p.add_argument("--max-results", type=int, default=10)
    p.add_argument("--exclude-self", action="store_true")
    p.add_argument("--ignore-attendee-working-hours", action="store_true")
    p.add_argument("--lookback-days", type=int, default=365)
    p.set_defaults(func=find_meeting_times_command)

    p = sub.add_parser("schedule-meeting", help="解析参会人、选时间并创建会议")
    p.add_argument("--attendee", action="append", required=True)
    p.add_argument("--optional-attendee", action="append")
    p.add_argument("--subject", required=True)
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--window-start")
    p.add_argument("--window-end")
    p.add_argument("--duration-minutes", type=int, default=60)
    p.add_argument("--interval-minutes", type=int)
    p.add_argument("--location")
    p.add_argument("--reminder-minutes", type=int, default=15)
    p.add_argument("--exclude-self", action="store_true")
    p.add_argument("--ignore-attendee-working-hours", action="store_true")
    p.add_argument("--skip-availability-check", action="store_true")
    p.add_argument("--send-invitations", action="store_true")
    p.add_argument("--confirm-send", action="store_true")
    p.add_argument("--lookback-days", type=int, default=365)
    _add_html_body(p)
    p.set_defaults(func=schedule_meeting_command)

    p = sub.add_parser("configure-dt", help="保存统一 DT 测试对象")
    p.add_argument("--person-query", action="append", required=True)
    p.add_argument("--sender", action="append", required=True)
    p.add_argument("--draft-recipient", required=True)
    p.add_argument("--subject-contains")
    p.add_argument("--search-limit", type=int, default=20)
    p.set_defaults(func=configure_dt)

    p = sub.add_parser("show-dt-config", help="显示统一 DT 配置和测试分组")
    p.set_defaults(func=show_dt_config)

    p = sub.add_parser("clear-dt-config", help="删除统一 DT 配置")
    p.set_defaults(func=clear_dt_config)

    p = sub.add_parser("dt-test", help="运行持续增长的分组 DT 套件")
    p.add_argument("--read-only", action="store_true")
    p.add_argument(
        "--group",
        action="append",
        choices=["atomic", "workflow-v03", "semantic-mail-v04", "calendar-v05", "weekly-report-v06"],
        help="只运行指定分组；可重复。默认运行全部分组。",
    )
    p.set_defaults(func=dt_test)

    p = sub.add_parser("version", help="显示已安装版本、包路径和 Python 路径")
    p.set_defaults(func=version_command)

    p = sub.add_parser("tool-list", help="显示 production/debug Agent 工具清单")
    p.add_argument("--profile", choices=["production", "debug"], default="production")
    p.set_defaults(func=tool_list)

    p = sub.add_parser("mcp-config", help="输出可粘贴到 MCP 客户端的 stdio 配置")
    p.add_argument("--debug-tools", action="store_true", help="输出包含 17 个工具的调试 server 配置")
    p.set_defaults(func=mcp_config)

    p = sub.add_parser("serve", help="启动 stdio MCP server")
    p.add_argument("--debug-tools", action="store_true", help="暴露 11 个生产工具和 6 个调试原语")
    p.set_defaults(func=serve)

    p = sub.add_parser("reset-local", help="删除本地配置、DT 配置、状态库和已保存密码")
    p.set_defaults(func=reset_local)

    p = sub.add_parser("logout", help="删除系统凭据库中的已保存密码")
    p.set_defaults(func=logout)
    return parser


def main() -> None:
    _configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args()
    try:
        code = args.func(args)
    except (ExchangeMcpError, ValueError, OSError, KeyError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    raise SystemExit(code)


if __name__ == "__main__":
    main()
