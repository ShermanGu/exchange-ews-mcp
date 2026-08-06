# -*- coding: utf-8 -*-
"""Exact Outlook HTML blocks accepted as weekly-report reply separators.

The values in this module are intentionally compared byte-for-byte at the
Python Unicode string level after EWS XML parsing.  Do not normalize, unescape,
reformat, or DOM-reserialize candidates before comparison.  Add a new entry
only after it has been observed and confirmed in the target Exchange/Outlook
environment.
"""
from __future__ import annotations

WEEKLY_REPORT_SEPARATOR_WHITELIST: dict[str, str] = {
    "outlook_dengxian_double_quoted": (
        '<p class="MsoNormal"><span lang="EN-US" style="font-family:等线">'
        '<o:p>&nbsp;</o:p></span></p>'
    ),
    "outlook_dengxian_unquoted_single_quoted": (
        "<p class=MsoNormal><span lang=EN-US style='font-family:等线'>"
        "<o:p>&nbsp;</o:p></span></p>"
    ),
}

WEEKLY_REPORT_SEPARATOR_NAME_BY_HTML: dict[str, str] = {
    html: name for name, html in WEEKLY_REPORT_SEPARATOR_WHITELIST.items()
}

MAX_WEEKLY_REPORT_SEPARATOR_LENGTH = max(
    len(value) for value in WEEKLY_REPORT_SEPARATOR_WHITELIST.values()
)
