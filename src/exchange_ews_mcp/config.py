from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from platformdirs import user_config_dir

from .calendar_utils import normalize_hhmm, validate_zone
from .errors import ConfigurationError

APP_NAME = "exchange-ews-mcp"
CONFIG_FILENAME = "config.json"


@dataclass(frozen=True)
class AppConfig:
    ews_url: str
    username: str
    verify_tls: bool = True
    timeout_seconds: int = 30
    exchange_version: str = "Exchange2010_SP2"
    attachment_roots: list[str] | None = None
    max_attachment_bytes: int = 10 * 1024 * 1024
    primary_email: str | None = None
    display_name: str | None = None
    company_email_domains: list[str] | None = None
    calendar_time_zone: str = "UTC"
    calendar_workday_start: str = "09:00"
    calendar_workday_end: str = "18:00"
    calendar_workdays: list[int] | None = None
    calendar_slot_interval_minutes: int = 30

    def validate(self) -> None:
        parsed = urlparse(self.ews_url)
        if parsed.scheme != "https":
            raise ConfigurationError("EWS URL 必须使用 https://。")
        if not parsed.netloc:
            raise ConfigurationError("EWS URL 缺少服务器域名。")
        if not parsed.path.lower().endswith("/ews/exchange.asmx"):
            raise ConfigurationError("EWS URL 通常应以 /EWS/Exchange.asmx 结尾。")
        if not self.username.strip():
            raise ConfigurationError("用户名不能为空。")
        if self.timeout_seconds <= 0:
            raise ConfigurationError("timeout_seconds 必须大于 0。")
        if self.max_attachment_bytes <= 0:
            raise ConfigurationError("max_attachment_bytes 必须大于 0。")
        if self.primary_email is not None and ("@" not in self.primary_email or not self.primary_email.strip()):
            raise ConfigurationError("primary_email 不是有效邮箱地址。")
        if self.display_name is not None and not self.display_name.strip():
            raise ConfigurationError("display_name 不能为空字符串。")
        if self.company_email_domains is not None:
            if not isinstance(self.company_email_domains, list):
                raise ConfigurationError("company_email_domains 必须是域名字符串列表。")
            for domain in self.company_email_domains:
                value = domain.strip() if isinstance(domain, str) else ""
                if not value or "@" in value or " " in value or value.startswith(".") or value.endswith("."):
                    raise ConfigurationError(f"无效的公司邮箱域名：{domain!r}")
        try:
            validate_zone(self.calendar_time_zone)
        except ValueError as exc:
            raise ConfigurationError(
                f"calendar_time_zone 不是有效 IANA 时区：{self.calendar_time_zone!r}。{exc}"
            ) from exc
        parsed_work_times: dict[str, int] = {}
        for field_name, value in (
            ("calendar_workday_start", self.calendar_workday_start),
            ("calendar_workday_end", self.calendar_workday_end),
        ):
            try:
                normalized = normalize_hhmm(value, field_name)
                hour, minute = (int(part) for part in normalized.split(":"))
                parsed_work_times[field_name] = hour * 60 + minute
            except ValueError as exc:
                raise ConfigurationError(str(exc)) from exc
        if parsed_work_times["calendar_workday_end"] <= parsed_work_times["calendar_workday_start"]:
            raise ConfigurationError("calendar_workday_end 必须晚于 calendar_workday_start。")
        if self.calendar_workdays is not None:
            if not isinstance(self.calendar_workdays, list) or not self.calendar_workdays:
                raise ConfigurationError("calendar_workdays 必须是非空整数列表。")
            if any(not isinstance(day, int) or not 0 <= day <= 6 for day in self.calendar_workdays):
                raise ConfigurationError("calendar_workdays 只允许 0..6，Monday=0。")
        if not 5 <= self.calendar_slot_interval_minutes <= 1440:
            raise ConfigurationError("calendar_slot_interval_minutes 必须在 5 到 1440 之间。")
        if self.attachment_roots is not None:
            if not isinstance(self.attachment_roots, list):
                raise ConfigurationError("attachment_roots 必须是路径字符串列表。")
            if any(not isinstance(root, str) or not root.strip() for root in self.attachment_roots):
                raise ConfigurationError("attachment_roots 中包含无效路径。")


def config_path() -> Path:
    return Path(user_config_dir(APP_NAME, appauthor=False)) / CONFIG_FILENAME


def normalize_config(config: AppConfig) -> AppConfig:
    """Return a canonical config suitable for persistence and display."""
    return replace(
        config,
        calendar_workday_start=normalize_hhmm(
            config.calendar_workday_start, "calendar_workday_start"
        ),
        calendar_workday_end=normalize_hhmm(
            config.calendar_workday_end, "calendar_workday_end"
        ),
    )


def save_config(config: AppConfig) -> Path:
    config = normalize_config(config)
    config.validate()
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)
    return path


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        raise ConfigurationError(
            f"尚未配置。请先运行 exchange-ews-mcp configure。配置路径：{path}"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = normalize_config(AppConfig(**raw))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"无法读取配置文件 {path}: {exc}") from exc
    config.validate()
    return config

def effective_company_domains(config: AppConfig) -> list[str]:
    """Return normalized configured domains plus the current user's SMTP domain."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in config.company_email_domains or []:
        value = raw.strip().casefold().removeprefix("@")
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    if config.primary_email and "@" in config.primary_email:
        inferred = config.primary_email.rsplit("@", 1)[1].strip().casefold()
        if inferred and inferred not in seen:
            result.append(inferred)
    return result

