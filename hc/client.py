from __future__ import annotations

import asyncio
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
    silent_connect: bool = False  # suppress connect errors during background probes
    silent: bool = False          # suppress all hints (auth, session, connect)
    socket_path: str | None = None  # Unix domain socket path (RUNTIME_SOCKET_PATH)

    def _make_transport(self) -> httpx.AsyncHTTPTransport | None:
        """Вернуть UDS-транспорт если задан socket_path, иначе None (httpx default)."""
        if self.socket_path:
            return httpx.AsyncHTTPTransport(uds=self.socket_path)
        return None

    def _async_client(self, *, timeout: float = 30.0) -> httpx.AsyncClient:
        transport = self._make_transport()
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            verify=self.verify_ssl,
            transport=transport,
        )

    def _auth_hint(self, status_code: int) -> None:
        if self.silent:
            return
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
            async with self._async_client(timeout=timeout) as client:
                return await client.request(method, path, headers=self._headers(), **kwargs)
        except httpx.ConnectError:
            if not self.silent_connect:
                hostport = self.base_url.replace("http://", "").replace("https://", "")
                console.print(f"[red]Ошибка: Core недоступен на {hostport}[/red]")
            return None
        except httpx.RequestError as e:
            if not self.silent_connect:
                console.print(f"[red]Ошибка: {e}[/red]")
            return None

    async def _try_refresh(self) -> bool:
        """Refresh access token via POST /auth/v1/refresh using stored session cookie.
        Updates self.token and calls on_token_refreshed on success."""
        if not self.refresh_token:
            return False
        try:
            async with self._async_client(timeout=10.0) as client:
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
        if self.silent:
            return
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

    async def _request_json_absolute(
        self,
        method: str,
        path: str,
        *,
        http_timeout: float = 10.0,
        return_error_json: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Request by absolute path (no /api prefix).

        ``http_timeout`` — таймаут HTTP в секундах.
        Если ``return_error_json`` и тело ошибки JSON-объект — вернуть его (иначе ``None``).
        """
        resp = await self._do_request(method, path, timeout=http_timeout, **kwargs)
        if resp is None:
            return None

        if resp.status_code == 401:
            if await self._try_refresh():
                resp = await self._do_request(method, path, timeout=http_timeout, **kwargs)
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
            if return_error_json:
                try:
                    err_obj = resp.json()
                    return err_obj if isinstance(err_obj, dict) else None
                except ValueError:
                    return {
                        "ok": False,
                        "error": (resp.text or "").strip() or f"HTTP {resp.status_code}",
                    }
            return None
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    async def _post_multipart_absolute(
        self,
        path: str,
        *,
        files: dict[str, Any],
        data: dict[str, str] | None,
        http_timeout: float = 300.0,
        return_error_json: bool = True,
    ) -> Any:
        """POST multipart without forcing JSON Content-Type (для install-upload)."""
        console = Console()
        for attempt in range(2):
            try:
                async with self._async_client(timeout=http_timeout) as client:
                    resp = await client.post(
                        path,
                        headers=dict(self._headers()),
                        files=files,
                        data=data,
                    )
            except httpx.ConnectError:
                hostport = self.base_url.replace("http://", "").replace("https://", "")
                console.print(f"[red]Ошибка: Core недоступен на {hostport}[/red]")
                return None
            except httpx.RequestError as e:
                console.print(f"[red]Ошибка: {e}[/red]")
                return None

            if resp.status_code == 401 and attempt == 0 and await self._try_refresh():
                continue

            if resp.status_code == 401:
                self._expired_session_hint()
                return None
            if resp.status_code == 403:
                self._auth_hint(403)
                return None
            if resp.is_error:
                if return_error_json:
                    try:
                        err_obj = resp.json()
                        return err_obj if isinstance(err_obj, dict) else None
                    except ValueError:
                        return {
                            "ok": False,
                            "error": (resp.text or "").strip() or f"HTTP {resp.status_code}",
                        }
                return None
            try:
                return resp.json()
            except ValueError:
                return {"raw": resp.text}

        return None

    async def _stream_sse(self, path: str, **kwargs: Any) -> AsyncGenerator[str, None]:
        console = Console()
        prefix = self.api_prefix or API_PREFIX_CANDIDATES[0]
        url = f"{prefix}{path}"
        max_reconnects = 5
        backoff_base = 0.5

        for reconnect in range(max_reconnects):
            for auth_attempt in range(2):
                try:
                    async with self._async_client(timeout=30000.0) as client:
                        async with client.stream(
                            "GET",
                            url,
                            headers={**self._headers(), "Accept": "text/event-stream"},
                            **kwargs,
                        ) as resp:
                            if resp.status_code == 401:
                                if auth_attempt == 0 and await self._try_refresh():
                                    continue
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
                    if reconnect < max_reconnects - 1:
                        await asyncio.sleep(backoff_base * (2**reconnect))
                        continue
                    hostport = self.base_url.replace("http://", "").replace("https://", "")
                    console.print(f"[red]Ошибка: Core недоступен на {hostport}[/red]")
                    return
                except httpx.RequestError as e:
                    if reconnect < max_reconnects - 1:
                        await asyncio.sleep(backoff_base * (2**reconnect))
                        continue
                    console.print(f"[red]Ошибка: {e}[/red]")
                    return
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
            async with self._async_client(timeout=30.0) as client:
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

    async def list_skills(self, plugin: str | None = None) -> dict[str, Any] | None:
        params = {"plugin": plugin} if plugin else None
        data = await self._request_json_absolute(
            "GET", endpoints.SKILLS_LIST, params=params
        )
        return data if isinstance(data, dict) else None

    async def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        data = await self._request_json_absolute(
            "GET", endpoints.SKILLS_GET.format(skill_id=skill_id)
        )
        return data if isinstance(data, dict) else None

    async def invoke_skill(self, skill_id: str, params: dict[str, Any]) -> dict[str, Any] | None:
        data = await self._request_json_absolute(
            "POST",
            endpoints.SKILLS_INVOKE.format(skill_id=skill_id),
            json={"params": params},
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

    async def core_version(self) -> dict[str, Any] | None:
        data = await self._request_json_absolute("GET", endpoints.VERSION)
        if not isinstance(data, dict):
            return None
        result = data.get("result")
        if isinstance(result, dict):
            return result
        return data

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

    async def admin_marketplace_install_archive(
        self,
        archive_path: str,
        *,
        sha256: str | None = None,
        http_timeout: float = 300.0,
    ) -> dict[str, Any] | None:
        """
        Установка плагина из архива через операцию ядра ``marketplace.install``.

        Важно: ``archive_path`` — путь **на машине (или в контейнере), где работает Core**,
        а не обязательно на хосте, с которого вы вызываете ``hc``.
        """
        body: dict[str, Any] = {"archive_path": archive_path}
        if sha256:
            body["sha256"] = sha256
        data = await self._request_json_absolute(
            "POST",
            endpoints.ADMIN_MARKETPLACE_INSTALL,
            json=body,
            http_timeout=http_timeout,
            return_error_json=True,
        )
        return data if isinstance(data, dict) else None

    async def admin_marketplace_install_upload_archive(
        self,
        local_path: str | Any,
        *,
        sha256: str | None = None,
        http_timeout: float = 300.0,
    ) -> dict[str, Any] | None:
        """Загрузить архив с локального диска и установить через ``install-upload`` (multipart)."""
        from pathlib import Path as PathClass

        p = PathClass(local_path)
        if not p.is_file():
            Console().print(f"[red]Файл не найден: {p}[/red]")
            return None

        data_form: dict[str, str] | None = {"sha256": sha256} if sha256 else None

        with p.open("rb") as fh:
            files = {"file": (p.name, fh, "application/octet-stream")}
            raw = await self._post_multipart_absolute(
                endpoints.ADMIN_MARKETPLACE_INSTALL_UPLOAD,
                files=files,
                data=data_form,
                http_timeout=http_timeout,
                return_error_json=True,
            )
        return raw if isinstance(raw, dict) else None

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

    # ------------------------------------------------------------------
    # Inspector — services & events
    # ------------------------------------------------------------------

    async def list_services_inspector(self) -> list[dict[str, Any]] | None:
        data = await self._request_json_absolute("GET", endpoints.ADMIN_INSPECTOR_SERVICES)
        if isinstance(data, dict):
            payload = data.get("result") or data.get("services") or data.get("data")
            if isinstance(payload, list):
                return payload
        if isinstance(data, list):
            return data
        return None

    async def list_events_inspector(self) -> list[dict[str, Any]] | None:
        data = await self._request_json_absolute("GET", endpoints.ADMIN_INSPECTOR_EVENTS)
        if isinstance(data, dict):
            payload = data.get("result") or data.get("events") or data.get("data")
            if isinstance(payload, list):
                return payload
        if isinstance(data, list):
            return data
        return None

    async def list_capabilities(self) -> list[dict[str, Any]] | None:
        data = await self._request_json_absolute("GET", endpoints.ADMIN_INSPECTOR_CAPABILITIES)
        if isinstance(data, dict):
            payload = data.get("result") or data.get("capabilities") or data.get("data")
            if isinstance(payload, list):
                return payload
        if isinstance(data, list):
            return data
        return None

    async def call_service(self, name: str, kwargs: dict[str, Any] | None = None) -> dict[str, Any] | None:
        url = endpoints.ADMIN_SERVICE_CALL.format(name=name)
        body: dict[str, Any] = {}
        if kwargs:
            body["kwargs"] = kwargs
        data = await self._request_json_absolute(
            "POST", url, json=body, return_error_json=True, http_timeout=30.0
        )
        return data if isinstance(data, dict) else None

    async def emit_event(self, event_type: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
        body = {"event_type": event_type, "data": data or {}}
        result = await self._request_json_absolute(
            "POST", endpoints.ADMIN_EVENT_EMIT, json=body, return_error_json=True
        )
        return result if isinstance(result, dict) else None

    async def stream_events(self, filter: str = "*") -> AsyncGenerator[dict[str, Any], None]:
        """Стримить live события с event bus через SSE."""
        import httpx as _httpx
        url = endpoints.ADMIN_INSPECTOR_EVENTS_STREAM
        params = {"filter": filter} if filter != "*" else {}
        try:
            async with self._async_client(timeout=30000.0) as client:
                async with client.stream(
                    "GET",
                    url,
                    headers={**self._headers(), "Accept": "text/event-stream"},
                    params=params,
                ) as resp:
                    if resp.status_code == 401:
                        self._expired_session_hint()
                        return
                    if resp.status_code == 403:
                        self._auth_hint(403)
                        return
                    if resp.is_error:
                        Console().print(f"[red]Ошибка: HTTP {resp.status_code}[/red]")
                        return
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        raw = line.removeprefix("data:").strip()
                        try:
                            import json
                            yield json.loads(raw)
                        except ValueError:
                            pass
        except _httpx.ConnectError:
            Console().print(f"[red]Ошибка: Core недоступен[/red]")
        except _httpx.RequestError as e:
            Console().print(f"[red]Ошибка: {e}[/red]")
