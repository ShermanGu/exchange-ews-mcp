from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from email.utils import parseaddr
from pathlib import Path

from platformdirs import user_config_dir

from .errors import ConfigurationError

DT_CONFIG_FILENAME = "dt-config.json"
VALID_GROUPS = ("atomic", "workflow-v03", "semantic-mail-v04", "calendar-v05", "template-mail-v06")


def _normalize_email(value: str, field_name: str) -> str:
    _, parsed = parseaddr(value.strip())
    if not parsed or "@" not in parsed:
        raise ConfigurationError(f"{field_name} 不是有效邮箱地址：{value!r}")
    return parsed


@dataclass(frozen=True)
class DtTestConfig:
    person_queries: list[str]
    senders: list[str]
    draft_recipient: str
    subject_contains: str | None = None
    search_limit: int = 20

    def normalized(self) -> "DtTestConfig":
        queries: list[str] = []
        seen_queries: set[str] = set()
        for raw in self.person_queries:
            value = raw.strip()
            if value and not value.isascii():
                raise ConfigurationError(
                    f"person_query 仅支持姓名拼音或完整邮箱：{value!r}"
                )
            key = value.casefold()
            if value and key not in seen_queries:
                queries.append(value)
                seen_queries.add(key)
        if not queries:
            raise ConfigurationError("至少需要一个 person_query。")

        senders: list[str] = []
        seen_senders: set[str] = set()
        for raw in self.senders:
            email = _normalize_email(raw, "senders")
            key = email.casefold()
            if key not in seen_senders:
                senders.append(email)
                seen_senders.add(key)
        if not senders:
            raise ConfigurationError("至少需要一个测试发件人邮箱。")

        if not 1 <= self.search_limit <= 100:
            raise ConfigurationError("search_limit 必须在 1 到 100 之间。")

        subject = self.subject_contains.strip() if self.subject_contains else None
        return DtTestConfig(
            person_queries=queries,
            senders=senders,
            draft_recipient=_normalize_email(self.draft_recipient, "draft_recipient"),
            subject_contains=subject or None,
            search_limit=self.search_limit,
        )


def dt_config_path() -> Path:
    return Path(user_config_dir("exchange-ews-mcp", appauthor=False)) / DT_CONFIG_FILENAME


def save_dt_config(config: DtTestConfig) -> Path:
    normalized = config.normalized()
    path = dt_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(normalized), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return path


def load_dt_config() -> DtTestConfig:
    path = dt_config_path()
    if not path.exists():
        raise ConfigurationError(
            "尚未配置 DT 对象。请先运行 exchange-ews-mcp configure-dt。"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = DtTestConfig(**raw)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ConfigurationError(f"无法读取 DT 配置 {path}: {exc}") from exc
    return config.normalized()


def delete_dt_config() -> bool:
    path = dt_config_path()
    if not path.exists():
        return False
    path.unlink()
    return True
