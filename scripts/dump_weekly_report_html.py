#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export weekly-report HTML and explain the history-header boundary decision.

Read-only: searches/reads one Exchange message and writes a local HTML report.
It never creates or updates a draft.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any


def _utc_after(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出最新周报并诊断发件人/From 历史边界与嵌套深度。")
    parser.add_argument("--subject-contains", default="周报")
    parser.add_argument("--folder", default="sentitems")
    parser.add_argument("--folders", nargs="+", default=None)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--item-id", default=None)
    parser.add_argument("--output", default="weekly_report_debug.html")
    return parser.parse_args()


def _load():
    from exchange_ews_mcp import __version__
    from exchange_ews_mcp.ews import normalize_mail_folders
    from exchange_ews_mcp.service import configured_client
    from exchange_ews_mcp.weekly_report import (
        _find_history_header_boundary,
        _scan_root_bounds,
        _visible_text_for_validation,
    )
    return {
        "version": __version__, "normalize": normalize_mail_folders,
        "client": configured_client(), "root_bounds": _scan_root_bounds,
        "find_boundary": _find_history_header_boundary,
        "visible_text": _visible_text_for_validation,
    }


def _message(args: argparse.Namespace, api: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    folders = api["normalize"](folder=args.folder, folders=args.folders, default_folder="sentitems")
    client = api["client"]
    if args.item_id:
        summary = {"item_id": args.item_id.strip()}
    else:
        if not args.subject_contains.strip():
            raise ValueError("--subject-contains 不能为空。")
        if not 1 <= args.lookback_days <= 3650:
            raise ValueError("--lookback-days 必须在 1 到 3650 之间。")
        result = client.search_emails_multi_folder(
            folders=folders, subject_contains=args.subject_contains.strip(),
            after=_utc_after(args.lookback_days), limit=1, offset=0,
        )
        items = list(result.get("items") or [])
        if not items:
            raise RuntimeError("没有找到匹配邮件。")
        summary = dict(items[0])
    item_id = str(summary.get("item_id") or "").strip()
    if not item_id:
        raise RuntimeError("搜索结果缺少 item_id。")
    full = client.get_email(item_id=item_id, change_key=summary.get("change_key"), max_body_chars=None)
    if full.get("body_truncated"):
        raise RuntimeError("邮件 Body 被标记为截断，无法可靠诊断。")
    return {**summary, **full}, folders


def _analyse(body: str, api: dict[str, Any]) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    start, end, root_name, root_offset = api["root_bounds"](body)
    boundary = api["find_boundary"](body, content_start=start, section_end=end, scan_limit=None)
    cut = boundary.boundary_offset if boundary is not None else end
    newest = body[start:cut]
    candidates: list[dict[str, Any]] = []
    if boundary is not None:
        candidates.append({
            "keyword": boundary.keyword,
            "text_offset": boundary.text_offset,
            "boundary_offset": boundary.boundary_offset,
            "depth": boundary.depth,
        })
    summary = {
        "scan_root": root_name,
        "root_offset": root_offset,
        "content_start": start,
        "content_end": end,
        "history_detected": boundary is not None,
        "history_keyword": boundary.keyword if boundary is not None else None,
        "history_text_offset": boundary.text_offset if boundary is not None else None,
        "history_boundary_offset": boundary.boundary_offset if boundary is not None else None,
        "history_keyword_depth": boundary.depth if boundary is not None else None,
        "visible_text_before_boundary": api["visible_text"](newest)[:1000],
    }
    return body[start:end], summary, candidates


def _output(message: dict[str, Any], folders: list[str], section: str, summary: dict[str, Any], candidates: list[dict[str, Any]], version: str) -> str:
    rows = ''.join(
        "<tr>"
        f"<td>{escape(str(c['keyword']))}</td><td>{c['text_offset']}</td>"
        f"<td>{c['boundary_offset']}</td><td>{c['depth']}</td></tr>"
        for c in candidates
    )
    metadata = {
        "exchange_ews_mcp_version": version, "subject": message.get("subject"),
        "item_id": message.get("item_id"), "conversation_id": message.get("conversation_id"),
        "folders": folders, "body_length": len(str(message.get("body_html") or "")), **summary,
    }
    meta = "\n".join(f"{k}: {v}" for k, v in metadata.items())
    return f'''<!doctype html>
<html><head><meta charset="utf-8"><title>Weekly Report HTML Debug</title>
<style>body{{font-family:Arial,sans-serif}} pre{{white-space:pre-wrap;border:1px solid #999;padding:12px}} table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #999;padding:6px;vertical-align:top}}</style>
</head><body>
<h1>Weekly report history-header boundary diagnostics</h1><pre>{escape(meta)}</pre>
<h2>First visible 发件人 / From match</h2><table><thead><tr><th>keyword</th><th>text offset</th><th>depth-0 boundary</th><th>nesting depth</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Scan root source (escaped)</h2><pre>{escape(section)}</pre>
<h2>Rendered scan root</h2>{section}
</body></html>'''


def main() -> int:
    try:
        args = _args(); api = _load(); message, folders = _message(args, api)
        body = str(message.get("body_html") or "")
        section, summary, candidates = _analyse(body, api)
        output = Path(args.output).expanduser().resolve(); output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_output(message, folders, section, summary, candidates, api["version"]), encoding="utf-8")
        print(f"Wrote: {output}")
        print(f"history_detected={summary['history_detected']} keyword={summary['history_keyword']}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=__import__('sys').stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
