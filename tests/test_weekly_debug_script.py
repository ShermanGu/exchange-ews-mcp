from __future__ import annotations

import importlib.util
from pathlib import Path

from exchange_ews_mcp.weekly_report import (
    _WORD_SECTION_OPEN_RE,
    _iter_word_section_paragraph_blocks,
    _matching_div_close,
    _visible_text_for_validation,
)
from exchange_ews_mcp.weekly_separator_whitelist import WEEKLY_REPORT_SEPARATOR_WHITELIST


def _load_script_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "dump_weekly_report_html.py"
    spec = importlib.util.spec_from_file_location("dump_weekly_report_html", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _api():
    return {
        "section_re": _WORD_SECTION_OPEN_RE,
        "iter_blocks": _iter_word_section_paragraph_blocks,
        "matching_close": _matching_div_close,
        "visible_text": _visible_text_for_validation,
        "whitelist": WEEKLY_REPORT_SEPARATOR_WHITELIST,
    }


def test_debug_script_reports_exact_match_and_nesting_depth() -> None:
    module = _load_script_module()
    separator = WEEKLY_REPORT_SEPARATOR_WHITELIST["outlook_dengxian_double_quoted"]
    near_match = (
        '<p class="MsoNormal" align="left"><span lang="EN-US" '
        'style="font-family:等线"><o:p>&nbsp;</o:p></span></p>'
    )
    body = (
        '<html><body><div class="WordSection1">'
        + near_match
        + '<table><tr><td>' + separator + '</td></tr></table>'
        + '<p>WK3</p>' + separator + '<p>WK2</p>'
        + '</div></body></html>'
    )
    section, summary, candidates = module._analyse(body, _api())
    assert "WordSection1" in section
    nested = next(c for c in candidates if c["whitelist_name"] and not c["direct_child"])
    assert nested["accepted"] is False
    assert nested["reason"] == "exact_whitelist_but_nested"
    accepted = next(c for c in candidates if c["accepted"])
    assert accepted["depth"] == 0
    assert accepted["reason"] == "accepted_exact_top_level_whitelist"
    assert summary["first_accepted_separator_offset"] == body.rindex(separator)
    assert "WK3" in summary["visible_text_before_first_accepted"]
    assert next(c for c in candidates if c["raw"] == near_match)["reason"] == "raw_html_not_in_whitelist"
