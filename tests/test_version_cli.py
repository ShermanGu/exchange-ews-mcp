from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from exchange_ews_mcp import __version__
from exchange_ews_mcp.cli import build_parser, version_command
from exchange_ews_mcp.config import AppConfig
from exchange_ews_mcp.ews import EwsClient


ROOT = Path(__file__).resolve().parents[1]


def test_version_is_v0616() -> None:
    assert __version__ == "0.6.16"


def test_version_command_registered() -> None:
    parser = build_parser()
    args = parser.parse_args(["version"])
    assert args.func is version_command


def test_tool_list_reconfigures_cp1252_stdio_to_utf8() -> None:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT / "src") + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    env["PYTHONIOENCODING"] = "cp1252"

    result = subprocess.run(
        [sys.executable, "-m", "exchange_ews_mcp.cli", "tool-list"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["profile"] == "production"
    assert "Agent" in payload["recommendation"]


def test_distribution_version_is_sourced_from_package_attribute() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = {attr = "exchange_ews_mcp.__version__"}' in pyproject
    assert '\nversion = "' not in pyproject


def test_installer_reads_expected_version_from_source() -> None:
    installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "src\\exchange_ews_mcp\\__init__.py" in installer
    assert "$ExpectedVersion = $VersionMatch.Groups[1].Value" in installer
    assert '$ExpectedVersion = "' not in installer
    assert "InstalledModuleVersion" in installer
    assert "InstalledMetadataVersion" in installer
    assert "Installed package path is outside the current virtual environment" in installer


def test_runtime_files_do_not_contain_stale_patch_versions() -> None:
    runtime_files = list((ROOT / "src" / "exchange_ews_mcp").glob("*.py"))
    runtime_files.append(ROOT / "install.ps1")
    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        assert "0.6.2" not in text, path
        assert "0.6.3" not in text, path
        assert "0.6.4" not in text, path
        assert "0.6.9" not in text, path
        assert "0.6.10" not in text, path
        assert "0.6.11" not in text, path


def test_ews_user_agent_uses_package_version() -> None:
    client = EwsClient(
        AppConfig(
            ews_url="https://mail.company.com/EWS/Exchange.asmx",
            username="DOMAIN\\user",
        ),
        "password",
    )
    assert client.session.headers["User-Agent"] == f"exchange-ews-mcp/{__version__}"
