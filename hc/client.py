from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import httpx
from rich.console import Console

from hc.api import API_PREFIX_CANDIDATES
from hc import api as endpoints


@dataclass(slots=True)
class HCClient:
    base_url: str
    token: str
    verify_ssl: bool = True
    api_prefix: str | None = None
    auth: str = "auto"  # auto|bearer|api-key

    def _auth_hint(self, status_code: int) -> None:
        console = Console()
        if status_code == 401:
            console.print("[yellow]Похоже, сессия истекла или токен неверный.[/yellow]")
        elif status_code == 403:
            console.print("[yellow]Похоже, не хватает прав для этой операции.[/yellow]")
        console.print("Проверь: `hc auth check`")
        console.print("Войти заново: `hc auth login -u admin`")
        console.print("Очистить токен: `hc auth logout`")

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        mode = (self.auth or "auto").lower()
        if mode == "bearer":
            return {"Authorization": f"Bearer {self.token}"}
        if mode in {"api-key", "apikey", "x-api-key"}:
            return {"X-API-Key": self.token}
        # auto
        if self.token.count(".") >= 2:
            return {"Authorization": f"Bearer {self.token}"}
        return {"X-API-Key": self.token}

    def _candidate_prefixes(self) -> tuple[str, ...]:
        if self.api_prefix:
            return (self.api_prefix,)
        return API_PREFIX_CANDIDATES

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        console = Console()
        last_resp: httpx.Response | None = None

        for prefix in self._candidate_prefixes():
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url, timeout=30.0, verify=self.verify_ssl
                ) as client:
                    resp = await client.request(
                        method,
                        f"{prefix}{path}",
                        headers=self._headers(),
                        **kwargs,
                    )
            except httpx.ConnectError:
                hostport = self.base_url.replace("http://", "").replace("https://", "")
                console.print(f"[red]Ошибка: Core недоступен на {hostport}[/red]")
                return None
            except httpx.RequestError as e:
                console.print(f"[red]Ошибка: {e}[/red]")
                return None

            last_resp = resp
            if resp.status_code == 404 and self.api_prefix is None:
                # Возможно, другой префикс API (например /api/v1).
                continue

            # Закэшируем рабочий префикс.
            if self.api_prefix is None:
                self.api_prefix = prefix

            if resp.status_code == 401:
                console.print("[red]Ошибка: 401 Unauthorized[/red]")
                self._auth_hint(401)
                return None
            if resp.status_code == 403:
                console.print("[red]Ошибка: 403 Forbidden[/red]")
                self._auth_hint(403)
                return None
            if resp.is_error:
                text = (resp.text or "").strip()
                console.print(f"[red]Ошибка: HTTP {resp.status_code} {text}[/red]")
                return None

            try:
                return resp.json()
            except ValueError:
                return {"raw": resp.text}

        # Все кандидаты дали 404.
        if last_resp is not None and last_resp.status_code == 404:
            console.print("[red]Ошибка: API endpoint не найден (проверь версию Core)[/red]")
            console.print("Подсказка: у некоторых сборок health на `/monitor/health` (без `/api`).")
        return None

    async def _request_json_optional(self, method: str, path: str, **kwargs: Any) -> Any:
        """Как `_request_json`, но 404 не считается ошибкой и не печатается."""
        console = Console()
        last_resp: httpx.Response | None = None

        for prefix in self._candidate_prefixes():
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url, timeout=30.0, verify=self.verify_ssl
                ) as client:
                    resp = await client.request(
                        method,
                        f"{prefix}{path}",
                        headers=self._headers(),
                        **kwargs,
                    )
            except httpx.ConnectError:
                hostport = self.base_url.replace("http://", "").replace("https://", "")
                console.print(f"[red]Ошибка: Core недоступен на {hostport}[/red]")
                return None
            except httpx.RequestError as e:
                console.print(f"[red]Ошибка: {e}[/red]")
                return None

            last_resp = resp
            if resp.status_code == 404 and self.api_prefix is None:
                continue

            if self.api_prefix is None:
                self.api_prefix = prefix

            if resp.status_code == 404:
                return None
            if resp.status_code == 401:
                console.print("[red]Ошибка: 401 Unauthorized[/red]")
                self._auth_hint(401)
                return None
            if resp.status_code == 403:
                console.print("[red]Ошибка: 403 Forbidden[/red]")
                self._auth_hint(403)
                return None
            if resp.is_error:
                return None
            try:
                return resp.json()
            except ValueError:
                return {"raw": resp.text}

        if last_resp is not None and last_resp.status_code == 404:
            return None
        return None

    async def _request_json_absolute(self, method: str, path: str, **kwargs: Any) -> Any:
        """Запрос по абсолютному пути (без /api префикса)."""
        console = Console()
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=10.0, verify=self.verify_ssl
            ) as client:
                resp = await client.request(method, path, headers=self._headers(), **kwargs)
        except httpx.ConnectError:
            hostport = self.base_url.replace("http://", "").replace("https://", "")
            console.print(f"[red]Ошибка: Core недоступен на {hostport}[/red]")
            return None
        except httpx.RequestError as e:
            console.print(f"[red]Ошибка: {e}[/red]")
            return None

        if resp.status_code == 401:
            console.print("[red]Ошибка: 401 Unauthorized[/red]")
            self._auth_hint(401)
            return None
        if resp.status_code == 403:
            console.print("[red]Ошибка: 403 Forbidden[/red]")
            self._auth_hint(403)
            return None
        if resp.is_error:
            return None
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    async def admin_status(self) -> dict[str, Any] | None:
        data = await self._request_json_absolute("GET", endpoints.ADMIN_STATUS)
        return data if isinstance(data, dict) else None

    async def auth_bootstrap(self) -> dict[str, Any] | None:
        data = await self._request_json_absolute("GET", endpoints.AUTH_BOOTSTRAP)
        return data if isinstance(data, dict) else None

    async def auth_initialize(self, user_id: str, username: str, password: str) -> dict[str, Any] | None:
        data = await self._request_json_absolute(
            "POST",
            endpoints.AUTH_INITIALIZE,
            json={"user_id": user_id, "username": username, "password": password},
        )
        return data if isinstance(data, dict) else None

    async def auth_login(self, user_id: str, password: str) -> dict[str, Any] | None:
        data = await self._request_json_absolute(
            "POST",
            endpoints.AUTH_LOGIN,
            json={"user_id": user_id, "password": password},
        )
        return data if isinstance(data, dict) else None

    async def auth_logout(self) -> dict[str, Any] | None:
        data = await self._request_json_absolute("POST", endpoints.AUTH_LOGOUT, json={})
        return data if isinstance(data, dict) else None

    async def auth_me(self) -> dict[str, Any] | None:
        data = await self._request_json_absolute("GET", endpoints.AUTH_ME)
        return data if isinstance(data, dict) else None

    async def api_keys_list(self) -> dict[str, Any] | None:
        data = await self._request_json_absolute("GET", endpoints.ADMIN_AUTH_API_KEYS)
        return data if isinstance(data, dict) else None

    async def api_keys_create(self, name: str | None = None) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        if name:
            payload["name"] = name
        data = await self._request_json_absolute("POST", endpoints.ADMIN_AUTH_API_KEYS, json=payload)
        return data if isinstance(data, dict) else None

    async def api_keys_revoke(self, key_id: str) -> dict[str, Any] | None:
        data = await self._request_json_absolute(
            "POST", endpoints.ADMIN_AUTH_API_KEYS_REVOKE, json={"key_id": key_id}
        )
        return data if isinstance(data, dict) else None

    async def api_keys_rotate(self, key_id: str) -> dict[str, Any] | None:
        data = await self._request_json_absolute(
            "POST", endpoints.ADMIN_AUTH_API_KEYS_ROTATE, json={"key_id": key_id}
        )
        return data if isinstance(data, dict) else None

    async def inspector_plugins(self) -> dict[str, Any] | None:
        data = await self._request_json_absolute("GET", endpoints.ADMIN_INSPECTOR_PLUGINS)
        return data if isinstance(data, dict) else None

    async def _stream_sse(self, path: str, **kwargs: Any) -> AsyncGenerator[str, None]:
        console = Console()
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=None, verify=self.verify_ssl
            ) as client:
                async with client.stream(
                    "GET",
                    f"{(self.api_prefix or API_PREFIX_CANDIDATES[0])}{path}",
                    headers={**self._headers(), "Accept": "text/event-stream"},
                    **kwargs,
                ) as resp:
                    if resp.status_code == 401:
                        console.print("[red]Ошибка: 401 Unauthorized[/red]")
                        self._auth_hint(401)
                        return
                    if resp.status_code == 403:
                        console.print("[red]Ошибка: 403 Forbidden[/red]")
                        self._auth_hint(403)
                        return
                    if resp.is_error:
                        console.print(f"[red]Ошибка: HTTP {resp.status_code}[/red]")
                        return
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data:"):
                            yield line.removeprefix("data:").lstrip()
        except httpx.ConnectError:
            hostport = self.base_url.replace("http://", "").replace("https://", "")
            console.print(f"[red]Ошибка: Core недоступен на {hostport}[/red]")
        except httpx.RequestError as e:
            console.print(f"[red]Ошибка: {e}[/red]")

    async def health(self) -> dict[str, Any] | None:
        # В core-runtime-service healthcheck обычно на /monitor/health (без /api).
        data = await self._request_json_absolute("GET", endpoints.MONITOR_HEALTH)
        if isinstance(data, dict):
            return data
        data = await self._request_json("GET", endpoints.HEALTH)
        return data if isinstance(data, dict) else None

    async def get_plugins(self) -> list[dict[str, Any]] | None:
        data = await self._request_json_optional("GET", endpoints.PLUGINS)
        return data if isinstance(data, list) else None

    async def install_plugin(self, name: str) -> AsyncGenerator[str, None]:
        async for msg in self._stream_sse(endpoints.PLUGIN_INSTALL.format(name=name)):
            yield msg

    async def remove_plugin(self, name: str) -> dict[str, Any] | None:
        data = await self._request_json("DELETE", endpoints.PLUGIN.format(name=name))
        return data if isinstance(data, dict) else None

    async def start_plugin(self, name: str) -> dict[str, Any] | None:
        data = await self._request_json("POST", endpoints.PLUGIN_START.format(name=name))
        return data if isinstance(data, dict) else None

    async def stop_plugin(self, name: str) -> dict[str, Any] | None:
        data = await self._request_json("POST", endpoints.PLUGIN_STOP.format(name=name))
        return data if isinstance(data, dict) else None

    async def get_plugin_info(self, name: str) -> dict[str, Any] | None:
        data = await self._request_json("GET", endpoints.PLUGIN.format(name=name))
        return data if isinstance(data, dict) else None

    async def get_modules(self) -> list[dict[str, Any]] | None:
        data = await self._request_json_optional("GET", endpoints.MODULES)
        return data if isinstance(data, list) else None

    async def stream_logs(self, module: str | None, follow: bool) -> AsyncGenerator[str, None]:
        params: dict[str, Any] = {"follow": str(follow).lower()}
        if module:
            params["module"] = module
        async for msg in self._stream_sse(endpoints.LOGS, params=params):
            yield msg

    async def get_marketplace_index(self) -> list[dict[str, Any]] | None:
        data = await self._request_json_optional("GET", endpoints.MARKETPLACE_INDEX)
        return data if isinstance(data, list) else None

    async def search_marketplace(self, query: str) -> list[dict[str, Any]] | None:
        data = await self._request_json_optional(
            "GET", endpoints.MARKETPLACE_SEARCH, params={"q": query}
        )
        return data if isinstance(data, list) else None

