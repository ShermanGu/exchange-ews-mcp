from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from email.utils import parseaddr
from pathlib import Path

from platformdirs import user_config_dir

from .errors import ConfigurationError

TEST_CONFIG_FILENAME = "v03-dt.json"


def _email(value: str, field: str) -> str:
    _, parsed = parseaddr(value.strip())
    if not parsed or "@" not in parsed:
        raise ConfigurationError(f"{field} 不是有效邮箱地址：{value!r}")
    return parsed


@dataclass(frozen=True)
class Phase023TestConfig:
    person_queries: list[str]
    sender: str
    draft_recipient: str
    subject_contains: str | None = None
    search_limit: int = 20

    def normalized(self) -> "Phase023TestConfig":
        queries: list[str] = []
        seen: set[str] = set()
        for raw in self.person_queries:
            value = raw.strip()
            if value and not value.isascii():
                raise ConfigurationError(
                    f"person_query 仅支持姓名拼音或完整邮箱：{value!r}"
                )
            if value and value.casefold() not in seen:
                queries.append(value)
                seen.add(value.casefold())
        if not queries:
            raise ConfigurationError("至少需要一个 person_query。")
        if not 1 <= self.search_limit <= 100:
            raise ConfigurationError("search_limit 必须在 1 到 100 之间。")
        subject = self.subject_contains.strip() if self.subject_contains else None
        return Phase023TestConfig(
            person_queries=queries,
            sender=_email(self.sender, "sender"),
            draft_recipient=_email(self.draft_recipient, "draft_recipient"),
            subject_contains=subject or None,
            search_limit=self.search_limit,
        )


def phase023_config_path() -> Path:
    return Path(user_config_dir("exchange-ews-mcp", appauthor=False)) / TEST_CONFIG_FILENAME


def save_phase023_config(config: Phase023TestConfig) -> Path:
    normalized = config.normalized()
    path = phase023_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(normalized), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return path


def load_phase023_config() -> Phase023TestConfig:
    path = phase023_config_path()
    if not path.exists():
        raise ConfigurationError(
            "尚未配置 v0.3 DT 对象。请先运行 exchange-ews-mcp configure-dt。"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = Phase023TestConfig(**raw)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ConfigurationError(f"无法读取 v0.3 DT 配置 {path}: {exc}") from exc
    return config.normalized()
