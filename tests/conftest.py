from __future__ import annotations

import sys
import types

try:
    import requests_ntlm  # noqa: F401
except ModuleNotFoundError:
    module = types.ModuleType("requests_ntlm")

    class HttpNtlmAuth:  # minimal unit-test fallback; real dependency is installed by install.cmd
        def __init__(self, username: str, password: str) -> None:
            self.username = username
            self.password = password

    module.HttpNtlmAuth = HttpNtlmAuth
    sys.modules["requests_ntlm"] = module

try:
    import keyring  # noqa: F401
except ModuleNotFoundError:
    module = types.ModuleType("keyring")
    _store: dict[tuple[str, str], str] = {}

    def set_password(service_name: str, username: str, password: str) -> None:
        _store[(service_name, username)] = password

    def get_password(service_name: str, username: str) -> str | None:
        return _store.get((service_name, username))

    def delete_password(service_name: str, username: str) -> None:
        _store.pop((service_name, username), None)

    class KeyringError(Exception):
        pass

    errors_module = types.ModuleType("keyring.errors")
    errors_module.KeyringError = KeyringError
    module.set_password = set_password
    module.get_password = get_password
    module.delete_password = delete_password
    module.errors = errors_module
    sys.modules["keyring"] = module
    sys.modules["keyring.errors"] = errors_module

try:
    from mcp.server.fastmcp import FastMCP  # noqa: F401
except ModuleNotFoundError:
    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:  # minimal registration surface for unit tests
        def __init__(self, name: str) -> None:
            self.name = name
            self._tools: list[object] = []

        def tool(self):
            def register(func):
                self._tools.append(func)
                return func
            return register

        def run(self, transport: str = "stdio") -> None:
            return None

    fastmcp_module.FastMCP = FastMCP
    server_module.fastmcp = fastmcp_module
    mcp_module.server = server_module
    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.server"] = server_module
    sys.modules["mcp.server.fastmcp"] = fastmcp_module
