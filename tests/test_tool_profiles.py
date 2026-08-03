import argparse
import json

from exchange_ews_mcp import cli


def test_tool_list_production_output(capsys) -> None:
    assert cli.tool_list(argparse.Namespace(profile="production")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["visible_tool_count"] == 18
    assert len(payload["production_tools"]) == 18
    assert len(payload["debug_only_tools"]) == 6
    assert "create_meeting" not in payload["visible_tools"]
    assert "schedule_meeting" in payload["visible_tools"]
    assert "extract_email_template" in payload["visible_tools"]
    assert "compose_from_email" not in payload["visible_tools"]
    assert "reply_from_email" not in payload["visible_tools"]


def test_mcp_config_defaults_to_production(capsys) -> None:
    assert cli.mcp_config(argparse.Namespace(debug_tools=False)) == 0
    payload = json.loads(capsys.readouterr().out)
    server = payload["mcpServers"]["exchange-ews"]
    assert server["args"] == ["-m", "exchange_ews_mcp.server"]
    assert payload["tool_profile"] == "production"


def test_mcp_config_can_select_debug(capsys) -> None:
    assert cli.mcp_config(argparse.Namespace(debug_tools=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    server = payload["mcpServers"]["exchange-ews-debug"]
    assert server["args"] == ["-m", "exchange_ews_mcp.debug_server"]
    assert payload["tool_profile"] == "debug"
