from __future__ import annotations

from datetime import timedelta
from typing import Any

from .calendar_utils import (
    Interval,
    apply_current_user_working_hours_override,
    configured_working_intervals,
    find_common_slots,
    is_exact_interval_available,
    format_utc,
    parse_input_datetime,
    parse_iso_datetime,
    decorate_availability_result,
    decorate_calendar_item,
    decorate_time_range,
)
from .config import AppConfig
from .ews import EwsClient
from .state_store import ReferenceStore
from .workflow import DEFAULT_HISTORY_DAYS, SemanticMailWorkflow, _valid_email


class CalendarWorkflow:
    """Deterministic meeting coordination built on EWS availability and calendar items."""

    def __init__(
        self,
        client: EwsClient,
        store: ReferenceStore,
        config: AppConfig,
        mail_workflow: SemanticMailWorkflow | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.config = config
        self.mail_workflow = mail_workflow or SemanticMailWorkflow(client, store, config)

    def _calendar_ref(self, item: dict[str, Any]) -> str | None:
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            return None
        return self.store.upsert_reference(
            kind="calendar",
            external_key=item_id,
            payload={
                "item_id": item_id,
                "change_key": item.get("change_key"),
                "subject": item.get("subject"),
                "start": item.get("start"),
                "end": item.get("end"),
                "folder": "calendar",
                "item_kind": "meeting",
                "is_meeting": True,
                "meeting_request_was_sent": item.get("meeting_request_was_sent"),
                "required_attendees": item.get("required_attendees") or [],
                "optional_attendees": item.get("optional_attendees") or [],
            },
            ttl_days=30,
        )

    def _current_user_email(self) -> str:
        configured = (self.config.primary_email or "").strip()
        if configured:
            return configured
        result = self.client.get_current_user()
        email = str(result.get("primary_email") or "").strip()
        if not email:
            raise ValueError("无法确定当前用户邮箱。请先运行 set-current-user。")
        return email

    def _resolve_attendees(
        self,
        queries: list[str],
        *,
        lookback_days: int,
    ) -> list[dict[str, Any]]:
        if not queries:
            raise ValueError("至少需要一个参会人。")
        slots: list[dict[str, Any]] = []
        for raw_query in queries:
            query = raw_query.strip()
            if not query:
                continue
            result = self.mail_workflow.resolve_people(
                query=query,
                lookback_days=lookback_days,
                auto_select=True,
            )
            selected = result.get("selected")
            if selected:
                slots.append(
                    {
                        "query": query,
                        "status": "resolved",
                        "email": selected.get("email"),
                        "person_ref": selected.get("person_ref"),
                        "selected": selected,
                        "default_rule_applied": result.get("default_rule_applied"),
                        "user_notice": result.get("user_notice"),
                        "candidates": result.get("candidates") or [],
                    }
                )
                continue
            direct = _valid_email(query)
            if direct and result.get("selection_status") == "not_found":
                slots.append(
                    {
                        "query": query,
                        "status": "resolved",
                        "email": direct,
                        "person_ref": None,
                        "selected": {"email": direct, "display_name": None},
                        "default_rule_applied": "direct_email_fallback",
                        "user_notice": None,
                        "candidates": [],
                    }
                )
                continue
            slots.append(
                {
                    "query": query,
                    "status": result.get("selection_status") or "needs_confirmation",
                    "email": None,
                    "person_ref": None,
                    "selected": None,
                    "default_rule_applied": None,
                    "user_notice": result.get("user_notice"),
                    "ambiguity_reason": result.get("ambiguity_reason"),
                    "candidates": result.get("candidates") or [],
                }
            )
        if not slots:
            raise ValueError("参会人列表不能为空。")
        return slots

    @staticmethod
    def _pending_attendees(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [slot for slot in slots if slot.get("status") != "resolved"]

    @staticmethod
    def _attendee_emails(slots: list[dict[str, Any]]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for slot in slots:
            email = str(slot.get("email") or "").strip()
            if email and email.casefold() not in seen:
                result.append(email)
                seen.add(email.casefold())
        return result

    def _apply_attendee_selections(
        self,
        slots: list[dict[str, Any]],
        selections: dict[str, str],
    ) -> list[dict[str, Any]]:
        for slot in slots:
            if slot.get("status") == "resolved":
                continue
            query = str(slot.get("query") or "")
            selected_value = selections.get(query) or selections.get(f"attendee:{query}")
            if not selected_value:
                continue
            chosen = next(
                (
                    candidate
                    for candidate in slot.get("candidates") or []
                    if selected_value in {candidate.get("person_ref"), candidate.get("email")}
                ),
                None,
            )
            if chosen is None:
                raise ValueError(f"选择 {selected_value!r} 不属于参会人查询 {query!r} 的候选。")
            slot.update(
                {
                    "status": "resolved",
                    "email": chosen.get("email"),
                    "person_ref": chosen.get("person_ref"),
                    "selected": chosen,
                    "default_rule_applied": "user_confirmation",
                    "user_notice": None,
                }
            )
        return slots

    def _execute_find_meeting_times(
        self,
        *,
        attendee_slots: list[dict[str, Any]],
        window_start: str,
        window_end: str,
        duration_minutes: int,
        interval_minutes: int | None,
        include_self: bool,
        max_results: int,
        respect_attendee_working_hours: bool,
    ) -> dict[str, Any]:
        start_dt = parse_input_datetime(
            window_start, self.config.calendar_time_zone, "window_start"
        )
        end_dt = parse_input_datetime(
            window_end, self.config.calendar_time_zone, "window_end"
        )
        if end_dt <= start_dt:
            raise ValueError("window_end 必须晚于 window_start。")
        if (end_dt - start_dt) > timedelta(days=42):
            raise ValueError("单次忙闲查询窗口不能超过 42 天。")
        interval = interval_minutes or self.config.calendar_slot_interval_minutes
        if not 5 <= interval <= 1440:
            raise ValueError("interval_minutes 必须在 5 到 1440 之间。")
        if not 5 <= duration_minutes <= 1440:
            raise ValueError("duration_minutes 必须在 5 到 1440 之间。")
        if not 1 <= max_results <= 50:
            raise ValueError("max_results 必须在 1 到 50 之间。")

        emails = self._attendee_emails(attendee_slots)
        current_email = self._current_user_email()
        request_attendees: list[dict[str, str]] = []
        if include_self:
            request_attendees.append({"email": current_email, "attendee_type": "Organizer"})
        for email in emails:
            if email.casefold() == current_email.casefold():
                continue
            request_attendees.append({"email": email, "attendee_type": "Required"})
        if not request_attendees:
            raise ValueError("没有可用于忙闲查询的参会人邮箱。")

        availability_raw = self.client.get_user_availability(
            attendees=request_attendees,
            start=format_utc(start_dt),
            end=format_utc(end_dt),
            interval_minutes=interval,
        )
        availability_raw = apply_current_user_working_hours_override(
            availability_raw,
            current_user_email=current_email,
            zone_name=self.config.calendar_time_zone,
            workday_start=self.config.calendar_workday_start,
            workday_end=self.config.calendar_workday_end,
            workdays=self.config.calendar_workdays or [0, 1, 2, 3, 4],
        )
        availability = decorate_availability_result(
            availability_raw, self.config.calendar_time_zone
        )
        failed = [item for item in availability.get("attendees") or [] if item.get("status") != "success"]
        fallback = configured_working_intervals(
            window_start=start_dt,
            window_end=end_dt,
            zone_name=self.config.calendar_time_zone,
            workday_start=self.config.calendar_workday_start,
            workday_end=self.config.calendar_workday_end,
            workdays=self.config.calendar_workdays or [0, 1, 2, 3, 4],
        )
        slots = [] if failed else find_common_slots(
            window_start=start_dt,
            window_end=end_dt,
            duration_minutes=duration_minutes,
            interval_minutes=interval,
            attendees=availability.get("attendees") or [],
            fallback_work_intervals=fallback,
            respect_attendee_working_hours=respect_attendee_working_hours,
            max_results=max_results,
            display_zone_name=self.config.calendar_time_zone,
        )
        notices = [
            str(slot["user_notice"])
            for slot in attendee_slots
            if slot.get("user_notice")
        ]
        result = {
            "status": "resolved" if slots else ("availability_error" if failed else "not_found"),
            "attendee_resolution": attendee_slots,
            "attendees": request_attendees,
            "availability": availability,
            "failed_attendees": failed,
            "window_start": format_utc(start_dt),
            "window_end": format_utc(end_dt),
            "duration_minutes": duration_minutes,
            "interval_minutes": interval,
            "calendar_time_zone": self.config.calendar_time_zone,
            "display_time_zone": self.config.calendar_time_zone,
            "transport_time_zone": "UTC",
            "fallback_working_hours": {
                "workdays": self.config.calendar_workdays or [0, 1, 2, 3, 4],
                "start": self.config.calendar_workday_start,
                "end": self.config.calendar_workday_end,
            },
            "respect_attendee_working_hours": respect_attendee_working_hours,
            "slots": slots,
            "returned": len(slots),
            "default_rule_notices": notices,
        }
        return decorate_time_range(
            result,
            start_key="window_start",
            end_key="window_end",
            zone_name=self.config.calendar_time_zone,
        )

    def find_meeting_times(
        self,
        *,
        attendee_queries: list[str],
        window_start: str,
        window_end: str,
        duration_minutes: int = 60,
        interval_minutes: int | None = None,
        include_self: bool = True,
        max_results: int = 10,
        respect_attendee_working_hours: bool = True,
        lookback_days: int = DEFAULT_HISTORY_DAYS,
    ) -> dict[str, Any]:
        slots = self._resolve_attendees(attendee_queries, lookback_days=lookback_days)
        pending = self._pending_attendees(slots)
        payload = {
            "attendee_queries": attendee_queries,
            "window_start": window_start,
            "window_end": window_end,
            "duration_minutes": duration_minutes,
            "interval_minutes": interval_minutes,
            "include_self": include_self,
            "max_results": max_results,
            "respect_attendee_working_hours": respect_attendee_working_hours,
            "lookback_days": lookback_days,
        }
        if pending:
            token = self.store.create_action_session(
                {"action": "find_meeting_times", "payload": payload, "attendee_slots": slots},
                ttl_hours=24,
            )
            return {
                "status": "needs_confirmation",
                "resume_token": token,
                "pending_attendees": pending,
                "slots": [],
                "display_time_zone": self.config.calendar_time_zone,
                "transport_time_zone": "UTC",
            }
        return self._execute_find_meeting_times(
            attendee_slots=slots,
            window_start=window_start,
            window_end=window_end,
            duration_minutes=duration_minutes,
            interval_minutes=interval_minutes,
            include_self=include_self,
            max_results=max_results,
            respect_attendee_working_hours=respect_attendee_working_hours,
        )

    def _create_meeting(
        self,
        *,
        attendee_slots: list[dict[str, Any]],
        subject: str,
        body_html: str,
        start: str,
        end: str,
        location: str | None,
        optional_attendees: list[str] | None,
        send_invitations: bool,
        reminder_minutes: int,
    ) -> dict[str, Any]:
        result = self.client.create_meeting(
            subject=subject,
            body_html=body_html,
            start=start,
            end=end,
            required_attendees=self._attendee_emails(attendee_slots),
            optional_attendees=optional_attendees or [],
            location=location,
            reminder_minutes=reminder_minutes,
            send_invitations=send_invitations,
        )
        item = decorate_calendar_item(
            result.as_dict(), self.config.calendar_time_zone
        )
        item["calendar_ref"] = self._calendar_ref(item)
        item["reference_kind"] = "calendar"
        item["update_tool"] = "update_meeting"
        item["send_tool"] = "send_meeting_invitation"
        notices = [str(slot["user_notice"]) for slot in attendee_slots if slot.get("user_notice")]
        return {
            "status": "meeting_sent" if send_invitations else "meeting_saved_not_sent",
            "calendar_item": item,
            "attendee_resolution": attendee_slots,
            "default_rule_notices": notices,
            "sent": send_invitations,
        }

    def schedule_meeting(
        self,
        *,
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
        lookback_days: int = DEFAULT_HISTORY_DAYS,
    ) -> dict[str, Any]:
        if not subject.strip():
            raise ValueError("subject 不能为空。")
        if not body_html.strip():
            raise ValueError("body_html 不能为空。")
        if not 0 <= reminder_minutes <= 40320:
            raise ValueError("reminder_minutes 必须在 0 到 40320 之间。")
        optional_attendees = optional_attendees or []
        invalid_optional = [value for value in optional_attendees if not _valid_email(value)]
        if invalid_optional:
            raise ValueError(
                "optional_attendees 当前只接受完整邮箱：" + ", ".join(invalid_optional)
            )
        attendee_slots = self._resolve_attendees(attendee_queries, lookback_days=lookback_days)
        pending = self._pending_attendees(attendee_slots)
        payload = {
            "attendee_queries": attendee_queries,
            "subject": subject,
            "body_html": body_html,
            "start": start,
            "end": end,
            "window_start": window_start,
            "window_end": window_end,
            "duration_minutes": duration_minutes,
            "interval_minutes": interval_minutes,
            "location": location,
            "optional_attendees": optional_attendees or [],
            "include_self_in_availability": include_self_in_availability,
            "respect_attendee_working_hours": respect_attendee_working_hours,
            "check_availability": check_availability,
            "send_invitations": send_invitations,
            "confirm_send": confirm_send,
            "reminder_minutes": reminder_minutes,
            "lookback_days": lookback_days,
        }
        if pending:
            token = self.store.create_action_session(
                {"action": "schedule_meeting", "payload": payload, "attendee_slots": attendee_slots},
                ttl_hours=24,
            )
            return {
                "status": "needs_confirmation",
                "confirmation_type": "attendees",
                "resume_token": token,
                "pending_attendees": pending,
                "sent": False,
            }

        selected_start = start
        selected_end = end
        availability_result: dict[str, Any] | None = None
        if bool(start) != bool(end):
            raise ValueError("start 和 end 必须同时提供。")
        if start and end:
            start_dt = parse_input_datetime(
                start, self.config.calendar_time_zone, "start"
            )
            end_dt = parse_input_datetime(
                end, self.config.calendar_time_zone, "end"
            )
            if end_dt <= start_dt:
                raise ValueError("end 必须晚于 start。")
            selected_start, selected_end = format_utc(start_dt), format_utc(end_dt)
            if check_availability:
                request_attendees = []
                current_email = self._current_user_email()
                if include_self_in_availability:
                    request_attendees.append({"email": current_email, "attendee_type": "Organizer"})
                request_attendees.extend(
                    {"email": email, "attendee_type": "Required"}
                    for email in self._attendee_emails(attendee_slots)
                    if email.casefold() != current_email.casefold()
                )
                availability_wire = self.client.get_user_availability(
                    attendees=request_attendees,
                    start=selected_start,
                    end=selected_end,
                    interval_minutes=self.config.calendar_slot_interval_minutes,
                )
                availability_wire = apply_current_user_working_hours_override(
                    availability_wire,
                    current_user_email=current_email,
                    zone_name=self.config.calendar_time_zone,
                    workday_start=self.config.calendar_workday_start,
                    workday_end=self.config.calendar_workday_end,
                    workdays=self.config.calendar_workdays or [0, 1, 2, 3, 4],
                )
                raw_availability = decorate_availability_result(
                    availability_wire, self.config.calendar_time_zone
                )
                fallback = configured_working_intervals(
                    window_start=start_dt,
                    window_end=end_dt,
                    zone_name=self.config.calendar_time_zone,
                    workday_start=self.config.calendar_workday_start,
                    workday_end=self.config.calendar_workday_end,
                    workdays=self.config.calendar_workdays or [0, 1, 2, 3, 4],
                )
                failed = [
                    item for item in raw_availability.get("attendees") or []
                    if item.get("status") != "success"
                ]
                exact_free = not failed and is_exact_interval_available(
                    candidate=Interval(start_dt, end_dt),
                    attendees=raw_availability.get("attendees") or [],
                    fallback_work_intervals=fallback,
                    respect_attendee_working_hours=respect_attendee_working_hours,
                )
                availability_result = {
                    "availability": raw_availability,
                    "failed_attendees": failed,
                    "exact_interval_available": exact_free,
                }
                if not exact_free:
                    return decorate_time_range(
                        {
                            "status": "time_conflict",
                            "requested_start": selected_start,
                            "requested_end": selected_end,
                            "availability": availability_result,
                            "sent": False,
                        },
                        start_key="requested_start",
                        end_key="requested_end",
                        zone_name=self.config.calendar_time_zone,
                    )
        else:
            if not window_start or not window_end:
                raise ValueError("未提供 start/end 时，必须提供 window_start/window_end。")
            availability_result = self._execute_find_meeting_times(
                attendee_slots=attendee_slots,
                window_start=window_start,
                window_end=window_end,
                duration_minutes=duration_minutes,
                interval_minutes=interval_minutes,
                include_self=include_self_in_availability,
                max_results=10,
                respect_attendee_working_hours=respect_attendee_working_hours,
            )
            suggestions = availability_result.get("slots") or []
            if not suggestions:
                return {**availability_result, "sent": False}
            if len(suggestions) > 1:
                token = self.store.create_action_session(
                    {
                        "action": "schedule_meeting",
                        "payload": payload,
                        "attendee_slots": attendee_slots,
                        "meeting_slots": suggestions,
                    },
                    ttl_hours=24,
                )
                return {
                    "status": "needs_confirmation",
                    "confirmation_type": "meeting_time",
                    "resume_token": token,
                    "pending_slots": suggestions,
                    "availability": availability_result,
                    "sent": False,
                }
            selected_start = suggestions[0]["start"]
            selected_end = suggestions[0]["end"]

        if not selected_start or not selected_end:
            raise ValueError("无法确定会议开始和结束时间。")

        if send_invitations and not confirm_send:
            token = self.store.create_action_session(
                {
                    "action": "schedule_meeting_send_confirmation",
                    "payload": payload,
                    "attendee_slots": attendee_slots,
                    "selected_start": selected_start,
                    "selected_end": selected_end,
                },
                ttl_hours=24,
            )
            return {
                "status": "needs_confirmation",
                "confirmation_type": "send_invitations",
                "resume_token": token,
                "meeting_summary": decorate_time_range(
                    {
                        "subject": subject,
                        "start": selected_start,
                        "end": selected_end,
                        "location": location,
                        "required_attendees": self._attendee_emails(attendee_slots),
                    },
                    start_key="start",
                    end_key="end",
                    zone_name=self.config.calendar_time_zone,
                ),
                "display_time_zone": self.config.calendar_time_zone,
                "transport_time_zone": "UTC",
                "user_notice": "该操作将向所有参会人发送会议邀请。确认后才会发送。",
                "sent": False,
            }

        return self._create_meeting(
            attendee_slots=attendee_slots,
            subject=subject,
            body_html=body_html,
            start=selected_start,
            end=selected_end,
            location=location,
            optional_attendees=optional_attendees,
            send_invitations=send_invitations,
            reminder_minutes=reminder_minutes,
        )

    def continue_action(self, *, resume_token: str, selections: dict[str, str]) -> dict[str, Any]:
        session = self.store.get_action_session(resume_token)
        if session["status"] not in {"pending", "needs_confirmation"}:
            raise ValueError(f"任务状态为 {session['status']}，不能继续。")
        state = session["state"]
        action = state.get("action")
        payload = dict(state.get("payload") or {})
        attendee_slots = self._apply_attendee_selections(
            state.get("attendee_slots") or [], selections
        )
        pending = self._pending_attendees(attendee_slots)
        if pending:
            self.store.update_action_session(
                resume_token,
                state={**state, "attendee_slots": attendee_slots},
            )
            return {
                "status": "needs_confirmation",
                "confirmation_type": "attendees",
                "resume_token": resume_token,
                "pending_attendees": pending,
                "sent": False,
            }

        if action == "find_meeting_times":
            result = self._execute_find_meeting_times(
                attendee_slots=attendee_slots,
                window_start=payload["window_start"],
                window_end=payload["window_end"],
                duration_minutes=int(payload.get("duration_minutes") or 60),
                interval_minutes=payload.get("interval_minutes"),
                include_self=bool(payload.get("include_self", True)),
                max_results=int(payload.get("max_results") or 10),
                respect_attendee_working_hours=bool(payload.get("respect_attendee_working_hours", True)),
            )
        elif action == "schedule_meeting":
            meeting_slots = state.get("meeting_slots") or []
            if meeting_slots:
                selected_value = selections.get("slot") or selections.get("start")
                if not selected_value:
                    return {
                        "status": "needs_confirmation",
                        "confirmation_type": "meeting_time",
                        "resume_token": resume_token,
                        "pending_slots": meeting_slots,
                        "sent": False,
                    }
                chosen = next(
                    (slot for slot in meeting_slots if selected_value in {slot.get("start"), f"{slot.get('start')}|{slot.get('end')}"}),
                    None,
                )
                if chosen is None:
                    raise ValueError(f"会议时间选择无效：{selected_value!r}")
                payload["start"] = chosen["start"]
                payload["end"] = chosen["end"]
                payload["window_start"] = None
                payload["window_end"] = None
            result = self.schedule_meeting(**payload)
        elif action == "schedule_meeting_send_confirmation":
            confirmation = (
                selections.get("confirm")
                or selections.get("action")
                or selections.get("send")
                or ""
            ).strip().casefold()
            for separator in (" ", "\t", "\r", "\n", ",", "，", "、", ";", "；"):
                confirmation = confirmation.replace(separator, "")
            send_values = {
                "send", "yes", "true", "confirm", "确认", "发送", "确认发送",
            }
            save_values = {
                "save", "saveonly", "no", "false", "donotsend", "dontsend",
                "保存", "仅保存", "只保存", "否", "不发送", "不需要发送",
                "不发送仅保存", "不发送仅仅保存", "不发送只保存",
            }
            cancel_values = {"cancel", "取消", "放弃"}
            if confirmation in cancel_values:
                result = {
                    "status": "cancelled",
                    "created": False,
                    "sent": False,
                }
            elif confirmation in send_values or confirmation in save_values:
                result = self._create_meeting(
                    attendee_slots=attendee_slots,
                    subject=payload["subject"],
                    body_html=payload["body_html"],
                    start=state["selected_start"],
                    end=state["selected_end"],
                    location=payload.get("location"),
                    optional_attendees=payload.get("optional_attendees") or [],
                    send_invitations=confirmation in send_values,
                    reminder_minutes=int(payload.get("reminder_minutes") or 15),
                )
            else:
                return {
                    "status": "needs_confirmation",
                    "confirmation_type": "send_invitations",
                    "resume_token": resume_token,
                    "user_notice": (
                        "请选择 confirm=send（创建并发送）、confirm=save（仅保存）"
                        "或 confirm=cancel（取消）。"
                    ),
                    "sent": False,
                }
        else:
            raise ValueError(f"不支持的日历恢复任务类型：{action!r}")

        self.store.update_action_session(
            resume_token,
            status="completed",
            state={**state, "attendee_slots": attendee_slots, "result": result},
        )
        return {**result, "resumed_from": resume_token}
