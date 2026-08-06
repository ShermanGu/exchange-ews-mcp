#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export WordSection1 and explain exact-whitelist separator decisions.

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
    parser = argparse.ArgumentParser(description="导出最新周报的第一个 WordSection1，并列出白名单和嵌套深度。")
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
        _WORD_SECTION_OPEN_RE,
        _iter_word_section_paragraph_blocks,
        _matching_div_close,
        _visible_text_for_validation,
    )
    from exchange_ews_mcp.weekly_separator_whitelist import WEEKLY_REPORT_SEPARATOR_WHITELIST
    return {
        "version": __version__, "normalize": normalize_mail_folders,
        "client": configured_client(), "section_re": _WORD_SECTION_OPEN_RE,
        "iter_blocks": _iter_word_section_paragraph_blocks,
        "matching_close": _matching_div_close,
        "visible_text": _visible_text_for_validation,
        "whitelist": WEEKLY_REPORT_SEPARATOR_WHITELIST,
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
    openings = list(api["section_re"].finditer(body))
    if not openings:
        raise RuntimeError("未找到 WordSection1。")
    opening = openings[0]
    closing = api["matching_close"](body, opening)
    section_end = closing[0] if closing else len(body)
    section_close_end = closing[1] if closing else len(body)
    blocks = list(api["iter_blocks"](
        body, content_start=opening.end(), section_end=section_end, scan_limit=None
    ))
    rows: list[dict[str, Any]] = []
    first_accepted = None
    for index, block in enumerate(blocks, start=1):
        accepted = block.accepted_separator
        if accepted and first_accepted is None:
            first_accepted = block
        rows.append({
            "index": index, "absolute_offset": block.start,
            "relative_offset": block.start - opening.end(), "depth": block.depth,
            "direct_child": block.is_direct_child,
            "whitelist_name": block.whitelist_name or "", "accepted": accepted,
            "reason": (
                "accepted_exact_top_level_whitelist" if accepted
                else "exact_whitelist_but_nested" if block.whitelist_name
                else "raw_html_not_in_whitelist"
            ),
            "raw": block.raw_html,
        })
    before = body[opening.end():first_accepted.start] if first_accepted else body[opening.end():section_end]
    summary = {
        "word_section_count": len(openings), "word_section_start": opening.start(),
        "word_section_content_start": opening.end(), "word_section_end": section_end,
        "paragraph_block_count": len(rows),
        "exact_whitelist_block_count": sum(1 for row in rows if row["whitelist_name"]),
        "accepted_separator_count": sum(1 for row in rows if row["accepted"]),
        "first_accepted_separator_offset": first_accepted.start if first_accepted else None,
        "visible_text_before_first_accepted": api["visible_text"](before)[:1000],
    }
    return body[opening.start():section_close_end], summary, rows


def _output(message: dict[str, Any], folders: list[str], section: str, summary: dict[str, Any], candidates: list[dict[str, Any]], version: str, whitelist: dict[str, str]) -> str:
    rows = []
    for c in candidates:
        rows.append(
            "<tr>"
            f"<td>{c['index']}</td><td>{c['absolute_offset']}</td><td>{c['relative_offset']}</td>"
            f"<td>{c['depth']}</td><td>{'YES' if c['direct_child'] else 'NO'}</td>"
            f"<td>{'YES' if c['accepted'] else 'NO'}</td><td>{escape(c['whitelist_name'])}</td>"
            f"<td>{escape(c['reason'])}</td><td><code>{escape(c['raw'])}</code></td></tr>"
        )
    whitelist_rows = ''.join(
        f"<tr><td>{escape(name)}</td><td><code>{escape(raw)}</code></td></tr>"
        for name, raw in whitelist.items()
    )
    metadata = {
        "exchange_ews_mcp_version": version, "subject": message.get("subject"),
        "item_id": message.get("item_id"), "conversation_id": message.get("conversation_id"),
        "folders": folders, "body_length": len(str(message.get("body_html") or "")), **summary,
    }
    meta = "\n".join(f"{k}: {v}" for k, v in metadata.items())
    return f'''<!doctype html>
<html><head><meta charset="utf-8"><title>Weekly Report HTML Debug</title>
<style>body{{font-family:Arial,sans-serif}} pre{{white-space:pre-wrap;border:1px solid #999;padding:12px}} table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #999;padding:6px;vertical-align:top}}code{{white-space:pre-wrap;word-break:break-all}}</style>
</head><body>
<h1>Weekly report exact-whitelist boundary diagnostics</h1><pre>{escape(meta)}</pre>
<h2>Exact separator whitelist</h2><table><thead><tr><th>name</th><th>exact raw HTML</th></tr></thead><tbody>{whitelist_rows}</tbody></table>
<h2>All complete p blocks in WordSection1</h2><table><thead><tr><th>#</th><th>absolute</th><th>relative</th><th>depth</th><th>direct child</th><th>accepted</th><th>whitelist</th><th>reason</th><th>raw HTML</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Exact WordSection1 source (escaped)</h2><pre>{escape(section)}</pre>
<h2>Rendered WordSection1</h2><!-- BEGIN EXACT WORDSECTION1 -->{section}<!-- END EXACT WORDSECTION1 -->
</body></html>'''


def main() -> int:
    try:
        args = _args(); api = _load(); message, folders = _message(args, api)
        body = str(message.get("body_html") or "")
        section, summary, candidates = _analyse(body, api)
        output = Path(args.output).expanduser().resolve(); output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_output(message, folders, section, summary, candidates, api["version"], api["whitelist"]), encoding="utf-8")
        print(f"Wrote: {output}")
        print(f"Paragraphs: {len(candidates)}; accepted: {sum(1 for c in candidates if c['accepted'])}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=__import__('sys').stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
