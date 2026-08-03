from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from email.utils import parseaddr
from pathlib import Path

from platformdirs import user_config_dir

from .errors import ConfigurationError

TEST_CONFIG_FILENAME = "phase2-test.json"


def _normalize_email(value: str, field_name: str) -> str:
    _, parsed = parseaddr(value.strip())
    if not parsed or "@" not in parsed:
        raise ConfigurationError(f"{field_name} 不是有效邮箱地址：{value!r}")
    return parsed


@dataclass(frozen=True)
class Phase2TestConfig:
    senders: list[str]
    draft_recipient: str
    subject_contains: str | None = None
    search_limit: int = 20

    def normalized(self) -> "Phase2TestConfig":
        senders: list[str] = []
        seen: set[str] = set()
        for raw in self.senders:
            parsed = _normalize_email(raw, "senders")
            lowered = parsed.lower()
            if lowered not in seen:
                senders.append(parsed)
                seen.add(lowered)
        if not senders:
            raise ConfigurationError("至少需要配置一个测试发件人邮箱。")
        recipient = _normalize_email(self.draft_recipient, "draft_recipient")
        if not 1 <= self.search_limit <= 100:
            raise ConfigurationError("search_limit 必须在 1 到 100 之间。")
        subject = self.subject_contains.strip() if self.subject_contains else None
        return Phase2TestConfig(
            senders=senders,
            draft_recipient=recipient,
            subject_contains=subject or None,
            search_limit=self.search_limit,
        )


def phase2_config_path() -> Path:
    return Path(user_config_dir("exchange-ews-mcp", appauthor=False)) / TEST_CONFIG_FILENAME


def save_phase2_config(config: Phase2TestConfig) -> Path:
    normalized = config.normalized()
    path = phase2_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(asdict(normalized), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)
    return path


def load_phase2_config() -> Phase2TestConfig:
    path = phase2_config_path()
    if not path.exists():
        raise ConfigurationError(
            "尚未配置第二阶段测试对象。请先运行 exchange-ews-mcp configure-tests。"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = Phase2TestConfig(**raw)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ConfigurationError(f"无法读取测试配置 {path}: {exc}") from exc
    return config.normalized()


def delete_phase2_config() -> bool:
    path = phase2_config_path()
    if not path.exists():
        return False
    path.unlink()
    return True
