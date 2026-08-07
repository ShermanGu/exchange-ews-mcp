from __future__ import annotations

from pathlib import Path

from exchange_ews_mcp import __version__
from exchange_ews_mcp.dt_config import VALID_GROUPS
from exchange_ews_mcp.tool_profiles import tool_names

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_release_repository_has_required_public_files() -> None:
    required = {
        ".github/workflows/ci.yml",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/pull_request_template.md",
        "README.md",
        "README.zh-CN.md",
        "docs/AGENT-CONNECTION.md",
        "docs/AGENT-TOOLS.md",
        "docs/ARCHITECTURE.md",
        "docs/WEEKLY-REPORT.md",
        "docs/DT.md",
        "docs/FRESH-START.md",
        "docs/CHANGELOG.md",
        "docs/RELEASE-CHECKLIST.md",
        "docs/CONTRIBUTING.md",
        "docs/CONTRIBUTING.zh-CN.md",
        "docs/SECURITY.md",
        "docs/SECURITY.zh-CN.md",
        "docs/CODE_OF_CONDUCT.md",
        "LICENSE",
    }
    missing = sorted(path for path in required if not (ROOT / path).is_file())
    assert missing == []


def test_root_keeps_only_readme_markdown_files() -> None:
    root_markdown = {path.name for path in ROOT.glob("*.md")}
    assert root_markdown == {"README.md", "README.zh-CN.md"}


def test_scripted_pytest_invocations_use_python_module_mode() -> None:
    unit = read("run-unit-tests.cmd")
    release = read("run-release-check.cmd")
    workflow = read(".github/workflows/ci.yml")
    checker = read("scripts/release_check.py")

    for text in (unit, release, workflow):
        assert "pytest.exe" not in text.lower()
    assert " -m pytest" in unit
    assert "python -m pytest" in workflow
    assert 'sys.executable, "-m", "pytest"' in checker
    assert '"build",\n            "--no-isolation"' in checker
    assert '"--no-build-isolation"' in checker
    assert "import setuptools, setuptools.build_meta, wheel" in checker

    # The release wrapper must not force a possibly incomplete project venv.
    # It first selects the Python command that can actually import pytest.
    assert 'python -c "import pytest"' in release
    assert "python scripts\\release_check.py" in release
    assert release.index('python -c "import pytest"') < release.index(':try_project_venv')


def test_public_docs_match_v0615_tool_and_workflow_contract() -> None:
    assert __version__ == "0.6.16"
    assert len(tool_names()) == 21
    assert len(tool_names(include_debug_tools=True)) == 27
    assert VALID_GROUPS == (
        "atomic",
        "workflow-v03",
        "semantic-mail-v04",
        "calendar-v05",
        "weekly-report-v06",
    )
    for name in ("README.md", "README.zh-CN.md", "docs/AGENT-CONNECTION.md", "docs/AGENT-TOOLS.md"):
        text = read(name)
        assert "0.6.16" in text
        assert "get_weekly_report_context" in text
        assert "update_weekly_report" in text
        assert "update_meeting" in text
        assert "send_meeting_invitation" in text


def test_ci_runs_supported_windows_matrix_and_package_build() -> None:
    workflow = read(".github/workflows/ci.yml")
    assert "windows-latest" in workflow
    for version in ("3.10", "3.11", "3.12", "3.13"):
        assert version in workflow
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" in workflow
    assert "error::ResourceWarning" in workflow
    assert "python -m build" in workflow
    assert "tool-list --profile debug" in workflow


def test_release_scripts_use_strict_unit_suite_and_keep_live_dt_explicit() -> None:
    unit = read("run-unit-tests.cmd")
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" in unit
    assert "PYTHONWARNINGS=error" in unit
    assert "error::ResourceWarning" in unit
    dt = read("run-dt-tests.cmd")
    assert "--read-only" in dt
    assert "--full" in dt
    checker = read("scripts/release_check.py")
    assert "Live Exchange DT must be run separately" in checker


def test_user_facing_docs_have_no_wrong_current_tool_counts() -> None:
    current_docs = (
        "README.md",
        "README.zh-CN.md",
        "docs/AGENT-CONNECTION.md",
        "docs/AGENT-TOOLS.md",
        "docs/ARCHITECTURE.md",
        "docs/WEEKLY-REPORT.md",
        "docs/DT.md",
        "docs/FRESH-START.md",
        "docs/CONTRIBUTING.md",
        "docs/CONTRIBUTING.zh-CN.md",
    )
    for name in current_docs:
        text = read(name)
        assert "Production profile: 18" not in text
        assert "visible_tool_count = 18" not in text
        assert "all 23 tools" not in text
        assert "完整 23" not in text


def test_release_checker_build_module_branch_disables_isolation(
    monkeypatch, tmp_path: Path
) -> None:
    import importlib.util
    import sys

    script_path = ROOT / "scripts" / "release_check.py"
    spec = importlib.util.spec_from_file_location("release_check_under_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls: list[list[str]] = []
    monkeypatch.setattr(module, "ensure_local_build_backend", lambda: None)
    monkeypatch.setattr(
        module.importlib.util,
        "find_spec",
        lambda name: object() if name == "build" else None,
    )
    monkeypatch.setattr(
        module,
        "run",
        lambda command, *, env=None: calls.append(command),
    )

    output_dir = tmp_path / "dist"
    module.build_distributions(output_dir)

    assert calls == [[
        sys.executable,
        "-m",
        "build",
        "--no-isolation",
        "--outdir",
        str(output_dir),
    ]]
