from exchange_ews_mcp import __version__
from exchange_ews_mcp.cli import build_parser, version_command


def test_version_is_v072():
    assert __version__ == "0.7.2"


def test_version_command_registered():
    parser = build_parser()
    args = parser.parse_args(["version"])
    assert args.func is version_command


def test_install_script_expected_version_matches_package():
    from pathlib import Path
    install_script = Path(__file__).resolve().parents[1] / "install.ps1"
    assert '$ExpectedVersion = "0.7.2"' in install_script.read_text(encoding="utf-8")
