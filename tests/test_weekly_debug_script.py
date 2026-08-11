from __future__ import annotations

import importlib.util
from pathlib import Path

from exchange_ews_mcp.weekly_report import (
    _find_history_header_boundary,
    _scan_root_bounds,
    _visible_text_for_validation,
)


def _load_script_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "dump_weekly_report_html.py"
    spec = importlib.util.spec_from_file_location("dump_weekly_report_html", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _api():
    return {
        "root_bounds": _scan_root_bounds,
        "find_boundary": _find_history_header_boundary,
        "visible_text": _visible_text_for_validation,
    }


def test_debug_script_reports_keyword_and_depth_zero_boundary() -> None:
    module = _load_script_module()
    body = (
        '<html><body><div class="WordSection1"><table><tr><td>WK3</td></tr></table>'
        '<div class="quoted"><table><tr><td><p><span>From: a@b.com</span></p></td></tr></table>'
        '<p>WK2</p></div></div></body></html>'
    )
    section, summary, candidates = module._analyse(body, _api())
    assert "WordSection1" not in section  # scan root is its inner HTML
    assert summary["scan_root"] == "word_section1"
    assert summary["history_detected"] is True
    assert summary["history_keyword"] == "From"
    assert summary["visible_text_before_boundary"] == "WK3"
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["depth"] >= 4
    assert body[candidate["boundary_offset"]:].startswith('<div class="quoted">')
