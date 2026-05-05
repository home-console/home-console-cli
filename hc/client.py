from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
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
    refresh_token: str = ""
    on_token_refreshed: Callable[[str], None] | None = field(default=None)

    def _auth_hint(self, status_code: int) -> None:
        console = Console()
        if status_code == 403:
            console.print("[yellow]Не хватает прав для этой операции.[/yellow]")
            console.print("Проверь роль: `hc auth whoami`")

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        mode = (self.auth or "auto").lower()
        if mode == "bearer":
            return {"Authorization": f"Bearer {self.token}"}
        if mode in {"api-key", "apikey", "x-api-key"}:
            return {"X-API-Key": self.token}
        # auto: JWT имеет ровно 2 точки
        if self.token.count(".") >= 2:
            return {"Authorization": f"Bearer {self.token}"}
        return {"X-API-Key": self.token}

    def _candidate_prefixes(self) -> tuple[str, ...]:
        if self.api_prefix:
            return (self.api_prefix,)
        return API_PREFIX_CANDIDATES

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    async def _do_request(
        self, method: str, path: str, *, timeout: float = 30.0, **kwargs: Any
    ) -> httpx.Response | None:
        console = Console()
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=timeout, verify=self.verify_ssl
            ) as client:
                return await client.request(method, path, headers=self._headers(), **kwargs)
        except httpx.ConnectError:
            hostport = self.base_url.replace("http://", "").replace("https://", "")
            console.print(f"[red]Ошибка: Core недоступен на {hostport}[/red]")
            return None
        except httpx.RequestError as e:
            console.print(f"[red]Ошибка: {e}[/red]")
            return None

    async def _try_refresh(self) -> bool:
        """Refresh access token via POST /auth/v1/refresh using stored session cookie.
        Updates self.token and calls on_token_refreshed on success."""
        if not self.refresh_token:
            return False
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=10.0, verify=self.verify_ssl
            ) as client:
                resp = await client.post(
                    endpoints.AUTH_REFRESH,
                    cookies={"session_id": self.refresh_token},
                )
            if resp.status_code != 200:
                return False
            data = resp.json()
            payload = data.get("result") if isinstance(data, dict) else None
            new_token = (
                (payload.get("access_token") if isinstance(payload, dict) else None)
                or (data.get("access_token") if isinstance(data, dict) else None)
            )
            if not new_token:
                return False
            self.token = str(new_token)
            if self.on_token_refreshed:
                self.on_token_refreshed(str(new_token))
            return True
        except Exception:  # noqa: BLE001
            return False

    def _expired_session_hint(self) -> None:
        console = Console()
        console.print("[red]Сессия истекла.[/red] Войдите заново: `hc auth login -u admin`")

    # ------------------------------------------------------------------
    # Request methods
    # ------------------------------------------------------------------

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        console = Console()
        last_resp: httpx.Response | None = None

        for prefix in self._candidate_prefixes():
            resp = await self._do_request(method, f"{prefix}{path}", **kwargs)
            if resp is None:
                return None

            last_resp = resp

            if resp.status_code == 404 and self.api_prefix is None:
                continue

            if self.api_prefix is None:
                self.api_prefix = prefix

            if resp.status_code == 401:
                if await self._try_refresh():
                    resp = await self._do_request(method, f"{prefix}{path}", **kwargs)
                    if resp is None:
                        return None
                    if not resp.is_error:
                        try:
                            return resp.json()
                        except ValueError:
                            return {"raw": resp.text}
                self._expired_session_hint()
                return None

            if resp.status_code == 403:
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

        if last_resp is not None and last_resp.status_code == 404:
            console.print("[red]Ошибка: API endpoint не найден (проверь версию Core)[/red]")
            console.print(
                "Подсказка: в актуальных сборках health на `/api/v1/monitor/health`, "
                "в старых — `/monitor/health`."
            )
        return None

    async def _request_json_optional(self, method: str, path: str, **kwargs: Any) -> Any:
        """Like _request_json but 404 is not an error and does not print."""
        last_resp: httpx.Response | None = None

        for prefix in self._candidate_prefixes():
            resp = await self._do_request(method, f"{prefix}{path}", **kwargs)
            if resp is None:
                return None

            last_resp = resp

            if resp.status_code == 404 and self.api_prefix is None:
                continue

            if self.api_prefix is None:
                self.api_prefix = prefix

            if resp.status_code == 404:
                return None

            if resp.status_code == 401:
                if await self._try_refresh():
                    resp = await self._do_request(method, f"{prefix}{path}", **kwargs)
                    if resp is None:
                        return None
                    if not resp.is_error:
                        try:
                            return resp.json()
                        except ValueError:
                            return {"raw": resp.text}
                self._expired_session_hint()
                return None

            if resp.status_code == 403:
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
        """Request by absolute path (no /api prefix)."""
        resp = await self._do_request(method, path, timeout=10.0, **kwargs)
        if resp is None:
            return None

        if resp.status_code == 401:
            if await self._try_refresh():
                resp = await self._do_request(method, path, timeout=10.0, **kwargs)
                if resp is None:
                    return None
                if not resp.is_error:
                    try:
                        return resp.json()
                    except ValueError:
                        return {"raw": resp.text}
            self._expired_session_hint()
            return None

        if resp.status_code == 403:
            self._auth_hint(403)
            return None
        if resp.is_error:
            return None
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    async def _stream_sse(self, path: str, **kwargs: Any) -> AsyncGenerator[str, None]:
        console = Console()
        url = f"{(self.api_prefix or API_PREFIX_CANDIDATES[0])}{path}"

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url, timeout=None, verify=self.verify_ssl
                ) as client:
                    async with client.stream(
                        "GET",
                        url,
                        headers={**self._headers(), "Accept": "text/event-stream"},
                        **kwargs,
                    ) as resp:
                        if resp.status_code == 401:
                            if attempt == 0 and await self._try_refresh():
                                continue  # retry with refreshed token
                            self._expired_session_hint()
                            return
                        if resp.status_code == 403:
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
                        return
            except httpx.ConnectError:
                hostport = self.base_url.replace("http://", "").replace("https://", "")
                console.print(f"[red]Ошибка: Core недоступен на {hostport}[/red]")
                return
            except httpx.RequestError as e:
                console.print(f"[red]Ошибка: {e}[/red]")
                return

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

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

    async def auth_login_full(
        self, user_id: str, password: str
    ) -> tuple[dict[str, Any] | None, str]:
        """Login and return (response_data, session_id_cookie) for refresh token storage."""
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=30.0, verify=self.verify_ssl
            ) as client:
                resp = await client.post(
                    endpoints.AUTH_LOGIN,
                    json={"user_id": user_id, "password": password},
                )
            if resp.is_error:
                return None, ""
            session_id = resp.cookies.get("session_id") or ""
            try:
                return resp.json(), session_id
            except ValueError:
                return None, ""
        except (httpx.ConnectError, httpx.RequestError):
            return None, ""

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

    async def list_users(self) -> dict[str, Any] | None:
        data = await self._request_json_absolute("GET", endpoints.ADMIN_AUTH_USERS)
        return data if isinstance(data, dict) else None

    async def create_user(
        self, user_id: str, username: str, password: str, is_admin: bool = False
    ) -> dict[str, Any] | None:
        data = await self._request_json_absolute(
            "POST",
            endpoints.ADMIN_AUTH_USERS,
            json={
                "user_id": user_id,
                "username": username,
                "password": password,
                "is_admin": is_admin,
            },
        )
        return data if isinstance(data, dict) else None

    async def list_sessions(self) -> dict[str, Any] | None:
        data = await self._request_json_absolute("GET", endpoints.ADMIN_AUTH_SESSIONS)
        return data if isinstance(data, dict) else None

    async def revoke_session(self, session_id: str) -> dict[str, Any] | None:
        data = await self._request_json_absolute(
            "POST", endpoints.ADMIN_AUTH_SESSIONS_REVOKE, json={"session_id": session_id}
        )
        return data if isinstance(data, dict) else None

    async def revoke_all_sessions(self) -> dict[str, Any] | None:
        data = await self._request_json_absolute(
            "POST", endpoints.ADMIN_AUTH_SESSIONS_REVOKE_ALL, json={}
        )
        return data if isinstance(data, dict) else None

    async def inspector_plugins(self) -> dict[str, Any] | None:
        data = await self._request_json_absolute("GET", endpoints.ADMIN_INSPECTOR_PLUGINS)
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------------
    # Plugins
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, Any] | None:
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

    async def reload_plugin(self, name: str) -> dict[str, Any] | None:
        data = await self._request_json_absolute("POST", endpoints.PLUGIN_RELOAD.format(name=name))
        return data if isinstance(data, dict) else None

    async def restart_plugin_container(self, name: str) -> dict[str, Any] | None:
        data = await self._request_json_absolute(
            "POST", endpoints.PLUGIN_RESTART_CONTAINER.format(name=name)
        )
        return data if isinstance(data, dict) else None

    async def get_plugin_info(self, name: str) -> dict[str, Any] | None:
        data = await self._request_json("GET", endpoints.PLUGIN.format(name=name))
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------

    async def get_modules(self) -> list[dict[str, Any]] | None:
        data = await self._request_json_optional("GET", endpoints.MODULES)
        return data if isinstance(data, list) else None

    async def start_module(self, name: str) -> dict[str, Any] | None:
        data = await self._request_json_optional("POST", endpoints.MODULE_START.format(name=name))
        return data if isinstance(data, dict) else None

    async def stop_module(self, name: str) -> dict[str, Any] | None:
        data = await self._request_json_optional("POST", endpoints.MODULE_STOP.format(name=name))
        return data if isinstance(data, dict) else None

    async def restart_module(self, name: str) -> dict[str, Any] | None:
        data = await self._request_json_optional("POST", endpoints.MODULE_RESTART.format(name=name))
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------------
    # Logs & Marketplace
    # ------------------------------------------------------------------

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

    async def get_marketplace_updates(self) -> list[dict[str, Any]]:
        """Сравнить версии установленных плагинов с каталогом marketplace."""
        installed = await self.get_plugins() or []
        catalog = await self.get_marketplace_index() or []

        catalog_map: dict[str, str] = {}
        for entry in catalog:
            if isinstance(entry, dict) and entry.get("name"):
                catalog_map[str(entry["name"])] = str(entry.get("version", ""))

        updates: list[dict[str, Any]] = []
        for plugin in installed:
            if not isinstance(plugin, dict):
                continue
            name = str(plugin.get("name", ""))
            current = str(plugin.get("version", ""))
            latest = catalog_map.get(name, "")
            if latest and latest != current:
                updates.append({"name": name, "current": current, "latest": latest})
        return updates
