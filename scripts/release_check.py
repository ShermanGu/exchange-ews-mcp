#!/usr/bin/env python3
"""Run repository-level release validation for Exchange EWS MCP.

This script is intentionally independent from a live mailbox. It validates the
repository contract, runs the strict deterministic test suite, compiles source,
and optionally builds distributions. Live EWS DT remains a separate, explicit
step because it requires the maintainer's Windows credentials and test mailbox.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.9.0"
EXPECTED_PRODUCTION_TOOLS = 11
EXPECTED_DEBUG_TOOLS = 17
EXPECTED_DT_GROUPS = (
    "atomic",
    "workflow-v03",
    "semantic-mail-v04",
    "calendar-v05",
    "weekly-report-v06",
)
REQUIRED_FILES = (
    ".github/workflows/ci.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/pull_request_template.md",
    ".gitignore",
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
    "docs/RELEASE-AUDIT.md",
    "docs/CONTRIBUTING.md",
    "docs/CONTRIBUTING.zh-CN.md",
    "docs/SECURITY.md",
    "docs/SECURITY.zh-CN.md",
    "docs/CODE_OF_CONDUCT.md",
    "LICENSE",
    "pyproject.toml",
    "install.cmd",
    "install.ps1",
    "run-unit-tests.cmd",
    "run-dt-tests.cmd",
    "run-release-check.cmd",
)


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def import_from_source():
    sys.path.insert(0, str(ROOT / "src"))
    from exchange_ews_mcp import __version__  # noqa: PLC0415
    from exchange_ews_mcp.dt_config import VALID_GROUPS  # noqa: PLC0415
    from exchange_ews_mcp.tool_profiles import tool_names  # noqa: PLC0415
    return __version__, tuple(VALID_GROUPS), tool_names


def static_checks() -> None:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]
    if missing:
        raise SystemExit("Missing release files: " + ", ".join(missing))

    root_markdown = {path.name for path in ROOT.glob("*.md")}
    expected_root_markdown = {"README.md", "README.zh-CN.md"}
    if root_markdown != expected_root_markdown:
        raise SystemExit(
            "Root Markdown files must be limited to the two README files: "
            + ", ".join(sorted(root_markdown))
        )

    command_pytest_invocations = {
        ROOT / "run-unit-tests.cmd": " -m pytest",
        ROOT / ".github/workflows/ci.yml": "python -m pytest",
    }
    for script_path, expected_invocation in command_pytest_invocations.items():
        script_text = script_path.read_text(encoding="utf-8")
        if "pytest.exe" in script_text.lower() or expected_invocation not in script_text:
            raise SystemExit(
                f"{script_path.relative_to(ROOT)} must invoke pytest through python -m pytest"
            )

    release_wrapper = (ROOT / "run-release-check.cmd").read_text(encoding="utf-8")
    if 'python -c "import pytest"' not in release_wrapper:
        raise SystemExit("run-release-check.cmd must select a Python interpreter that can import pytest")
    if "python scripts\\release_check.py" not in release_wrapper:
        raise SystemExit("run-release-check.cmd must prefer the current shell Python")

    ci_text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    expected_ci_artifact = f"exchange-ews-mcp-v{EXPECTED_VERSION}-distributions"
    if expected_ci_artifact not in ci_text:
        raise SystemExit(f"CI distribution artifact must be named {expected_ci_artifact}")

    release_audit = (ROOT / "docs/RELEASE-AUDIT.md").read_text(encoding="utf-8")
    if f"v{EXPECTED_VERSION}" not in release_audit:
        raise SystemExit("docs/RELEASE-AUDIT.md is not synchronized to the release version")
    if f"Production tool profile: **{EXPECTED_PRODUCTION_TOOLS}** tools" not in release_audit:
        raise SystemExit("docs/RELEASE-AUDIT.md has a stale Production tool count")
    if f"Debug tool profile: **{EXPECTED_DEBUG_TOOLS}** tools" not in release_audit:
        raise SystemExit("docs/RELEASE-AUDIT.md has a stale Debug tool count")

    checker_text = (ROOT / "scripts/release_check.py").read_text(encoding="utf-8")
    if 'sys.executable, "-m", "pytest"' not in checker_text:
        raise SystemExit("scripts/release_check.py must invoke pytest through python -m pytest")
    if '"--no-isolation"' not in checker_text:
        raise SystemExit("scripts/release_check.py must disable build isolation")
    if '"--no-build-isolation"' not in checker_text:
        raise SystemExit("pip wheel fallback must disable build isolation")
    if "import setuptools, setuptools.build_meta, wheel" not in checker_text:
        raise SystemExit("release check must preflight the local setuptools build backend")

    version, groups, tool_names = import_from_source()
    if version != EXPECTED_VERSION:
        raise SystemExit(f"Version mismatch: expected {EXPECTED_VERSION}, got {version}")
    if len(tool_names()) != EXPECTED_PRODUCTION_TOOLS:
        raise SystemExit("Unexpected production tool count")
    if len(tool_names(include_debug_tools=True)) != EXPECTED_DEBUG_TOOLS:
        raise SystemExit("Unexpected debug tool count")
    if groups != EXPECTED_DT_GROUPS:
        raise SystemExit(f"Unexpected DT groups: {groups!r}")

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if 'dynamic = ["version"]' not in pyproject:
        raise SystemExit("pyproject.toml must source the version dynamically")
    for doc in ("README.md", "README.zh-CN.md", "docs/AGENT-CONNECTION.md", "docs/AGENT-TOOLS.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        if EXPECTED_VERSION not in text:
            raise SystemExit(f"{doc} does not mention {EXPECTED_VERSION}")
        if "weekly_report" not in text or "continue_action" not in text:
            raise SystemExit(f"{doc} does not document the weekly_report -> continue_action contract")
        if "save_meeting" not in text or "send_meeting_invitation" not in text:
            raise SystemExit(f"{doc} does not document both saved-meeting tools")

    print(
        f"Static release contract OK: v{version}, "
        f"{EXPECTED_PRODUCTION_TOOLS}/{EXPECTED_DEBUG_TOOLS} tools, "
        f"{len(groups)} DT groups."
    )


def ensure_local_build_backend() -> None:
    """Verify the selected interpreter has the declared local build backend.

    The release checker intentionally builds without PEP 517 isolation. Corporate
    or offline environments often cannot populate pip's temporary build
    environment even though the selected interpreter already has setuptools and
    wheel installed.
    """

    command = [
        sys.executable,
        "-c",
        (
            "import setuptools, setuptools.build_meta, wheel; "
            "print('Local build backend OK: setuptools=' + setuptools.__version__ "
            "+ ', wheel=' + wheel.__version__)"
        ),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode == 0:
        if completed.stdout.strip():
            print(completed.stdout.strip())
        return

    details = completed.stderr.strip() or completed.stdout.strip() or "unknown import error"
    raise SystemExit(
        "The selected Python can run pytest but cannot import the local build backend "
        "(setuptools.build_meta and wheel).\n"
        f"Interpreter: {sys.executable}\n"
        f"Details: {details}\n"
        "Install or repair the build dependencies with:\n"
        f'  "{sys.executable}" -m pip install --upgrade setuptools wheel build'
    )


def build_distributions(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for child in output_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    ensure_local_build_backend()

    if importlib.util.find_spec("build"):
        # Do not create a temporary isolated build environment here. The wrapper
        # has already selected a working Python interpreter, and enterprise/offline
        # pip indexes may be unable to seed isolation with setuptools.build_meta.
        run([
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(output_dir),
        ])
    else:
        print("Python package 'build' is unavailable; building wheel with pip instead.")
        run([
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(output_dir),
            ".",
        ])


def write_sha256_manifest(output_dir: Path) -> Path:
    """Write SHA-256 checksums for built distribution artifacts."""
    artifacts = sorted(
        path for path in output_dir.iterdir()
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    if not artifacts:
        raise SystemExit(f"No distribution artifacts found in {output_dir}")

    lines = []
    for artifact in artifacts:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f"{digest}  {artifact.name}")
    manifest = output_dir / "SHA256SUMS.txt"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    resolved_manifest = manifest.resolve()
    try:
        display_path = resolved_manifest.relative_to(ROOT)
    except ValueError:
        display_path = resolved_manifest
    print(f"Wrote checksum manifest: {display_path}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--dist-dir", default="dist")
    args = parser.parse_args()

    static_checks()
    if not args.skip_tests:
        env = os.environ.copy()
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        env["PYTHONWARNINGS"] = "default"
        run([sys.executable, "-m", "pytest", "-W", "error::ResourceWarning"], env=env)
        run([sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts"])
    if not args.skip_build:
        dist_dir = ROOT / args.dist_dir
        build_distributions(dist_dir)
        write_sha256_manifest(dist_dir)
    print("Release validation completed. Live Exchange DT must be run separately; see docs/DT.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
