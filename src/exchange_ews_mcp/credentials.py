from __future__ import annotations

import keyring
from keyring.errors import KeyringError

from .errors import CredentialError

SERVICE_NAME = "exchange-ews-mcp"


def store_password(username: str, password: str) -> None:
    if not password:
        raise CredentialError("密码不能为空。")
    try:
        keyring.set_password(SERVICE_NAME, username, password)
    except KeyringError as exc:
        raise CredentialError(f"无法写入系统凭据库：{exc}") from exc


def get_password(username: str) -> str:
    try:
        password = keyring.get_password(SERVICE_NAME, username)
    except KeyringError as exc:
        raise CredentialError(f"无法读取系统凭据库：{exc}") from exc
    if not password:
        raise CredentialError(
            "未找到已保存密码。请重新运行 exchange-ews-mcp configure。"
        )
    return password


def delete_password(username: str) -> None:
    try:
        keyring.delete_password(SERVICE_NAME, username)
    except keyring.errors.PasswordDeleteError:
        return
    except KeyringError as exc:
        raise CredentialError(f"无法删除系统凭据：{exc}") from exc
