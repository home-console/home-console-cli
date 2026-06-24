"""hc plugin publish — упаковать плагин в zip и опубликовать в marketplace."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from hc.plugin_validator import validate_plugin


# ---------------------------------------------------------------------------
# Подпись архива
# ---------------------------------------------------------------------------

def _sign(archive_bytes: bytes) -> tuple[str, str]:
    """Ephemeral Ed25519: sign sha256(archive). Returns (sig_b64, pubkey_b64)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = Ed25519PrivateKey.generate()
    digest = hashlib.sha256(archive_bytes).digest()
    sig_b64 = base64.b64encode(key.sign(digest)).decode()
    pub_b64 = base64.b64encode(
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    return sig_b64, pub_b64


# ---------------------------------------------------------------------------
# Упаковка в zip
# ---------------------------------------------------------------------------

def _build_zip(plugin_path: Path) -> bytes:
    """Упаковать папку плагина в zip. plugin.json всегда в корне архива."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(plugin_path.rglob("*")):
            if file.is_file() and not _should_exclude(file):
                arcname = file.relative_to(plugin_path)
                zf.write(file, arcname)
    return buf.getvalue()


_EXCLUDE_PATTERNS = {
    "__pycache__", ".DS_Store", ".git", ".venv", "*.pyc", "*.pyo",
    "node_modules", ".pytest_cache", "dist", "build",
}


def _should_exclude(path: Path) -> bool:
    for part in path.parts:
        if part in _EXCLUDE_PATTERNS or part.endswith(".pyc") or part.endswith(".pyo"):
            return True
    return False


# ---------------------------------------------------------------------------
# Публикация
# ---------------------------------------------------------------------------

def _post_release(
    api_url: str,
    token: str,
    name: str,
    version: str,
    archive_bytes: bytes,
    sig_b64: str,
    pub_b64: str,
    *,
    channel: str,
    changelog: str,
    force: bool,
) -> httpx.Response:
    metadata = json.dumps({
        "version": version,
        "channel": channel,
        "changelog": changelog,
        "git_sha": "",
        "source_repo": "",
        "min_runtime": "1.0.0",
    })
    headers = {"Authorization": f"Bearer {token}"}
    files = {"archive": (f"{name}-{version}.zip", archive_bytes, "application/zip")}
    data = {"metadata": metadata, "signature": sig_b64, "public_key": pub_b64}

    if force:
        url = f"{api_url}/api/plugins/{name}/releases/{version}"
        return httpx.put(url, headers=headers, files=files, data=data, timeout=120.0)
    else:
        url = f"{api_url}/api/plugins/{name}/releases"
        return httpx.post(url, headers=headers, files=files, data=data, timeout=120.0)


# ---------------------------------------------------------------------------
# Команда
# ---------------------------------------------------------------------------

def register_publish(plugin_app: typer.Typer) -> None:
    @plugin_app.command("publish")
    def publish(
        path: Path = typer.Argument(..., help="Путь к папке плагина"),
        api: str | None = typer.Option(
            None, "--api",
            envvar="MARKETPLACE_API_URL",
            help="URL marketplace-api (или MARKETPLACE_API_URL)",
        ),
        token: str | None = typer.Option(
            None, "--token",
            envvar="MARKETPLACE_PUBLISHER_TOKEN",
            help="Publisher Bearer token (или MARKETPLACE_PUBLISHER_TOKEN)",
        ),
        channel: str = typer.Option("stable", "--channel", help="Канал: stable | dev"),
        force: bool = typer.Option(
            False, "--force", "-f",
            help="Перезаписать существующую версию (PUT replace)",
        ),
        changelog: str = typer.Option("", "--changelog", help="Текст changelog"),
        dry_run: bool = typer.Option(
            False, "--dry-run",
            help="Только валидация + сборка zip, без загрузки",
        ),
        skip_validate: bool = typer.Option(
            False, "--skip-validate",
            help="Пропустить валидацию перед публикацией",
        ),
    ) -> None:
        """Упаковать плагин в zip и опубликовать в marketplace.

        Шаги: validate → zip → подпись → POST /plugins/{name}/releases

        Требует: MARKETPLACE_ALLOW_RAW_UPLOAD=true на стороне marketplace-api.

        Примеры:
          hc plugin publish ./my_plugin --api https://marketplace.homeconsole.su --token $TOKEN
          hc plugin publish ./my_plugin --force --changelog "fix: auth bug"
          hc plugin publish ./my_plugin --dry-run
        """
        console = Console()
        plugin_path = path.resolve()

        if not plugin_path.is_dir():
            console.print(f"[red]Ошибка:[/red] '{plugin_path}' — не папка плагина")
            raise typer.Exit(code=2)

        # 1. Validate
        if not skip_validate:
            console.print(f"[dim]Валидация {plugin_path.name}…[/dim]")
            result = validate_plugin(plugin_path)
            if result.errors:
                for e in result.errors:
                    console.print(f"  [red]✗[/red] {e}")
                console.print("[red]Валидация не пройдена. Исправь ошибки и повтори.[/red]")
                raise typer.Exit(code=1)
            for w in result.warnings:
                console.print(f"  [yellow]⚠[/yellow] {w}")
            console.print(f"[green]✓[/green] Валидация пройдена")

        # 2. Read plugin.json
        pj_path = plugin_path / "plugin.json"
        try:
            pj = json.loads(pj_path.read_text(encoding="utf-8"))
        except Exception as exc:
            console.print(f"[red]Ошибка чтения plugin.json: {exc}[/red]")
            raise typer.Exit(code=1)

        name = pj.get("name", "")
        version = pj.get("version", "")
        if not name or not version:
            console.print("[red]plugin.json должен содержать name и version[/red]")
            raise typer.Exit(code=1)

        # 3. Build zip
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
            t = p.add_task(f"Упаковка {name} v{version}…", total=None)
            archive_bytes = _build_zip(plugin_path)
            p.update(t, description=f"Упаковано: {len(archive_bytes) / 1024:.1f} KB")

        sha256_hex = hashlib.sha256(archive_bytes).hexdigest()
        console.print(f"[dim]sha256: {sha256_hex}[/dim]")

        if dry_run:
            # Сохранить zip во временный файл для проверки
            with tempfile.NamedTemporaryFile(
                suffix=f"-{name}-{version}.zip", delete=False
            ) as tmp:
                tmp.write(archive_bytes)
                tmp_path = tmp.name
            console.print(f"[green]✓[/green] dry-run: zip сохранён в {tmp_path}")
            console.print("[dim]Загрузка пропущена (--dry-run)[/dim]")
            return

        # 4. Check credentials
        api_url = (api or "").rstrip("/")
        if not api_url:
            console.print(
                "[red]Не задан URL marketplace-api: --api или MARKETPLACE_API_URL[/red]"
            )
            raise typer.Exit(code=2)
        if not token:
            console.print(
                "[red]Не задан publisher token: --token или MARKETPLACE_PUBLISHER_TOKEN[/red]"
            )
            raise typer.Exit(code=2)

        # 5. Sign
        console.print("[dim]Подпись архива…[/dim]")
        sig_b64, pub_b64 = _sign(archive_bytes)

        # 6. Upload
        action = "PUT (replace)" if force else "POST"
        console.print(f"[dim]{action} → {api_url}/api/plugins/{name}/releases[/dim]")

        try:
            resp = _post_release(
                api_url, token, name, version, archive_bytes,
                sig_b64, pub_b64,
                channel=channel, changelog=changelog, force=force,
            )
        except httpx.HTTPError as exc:
            console.print(f"[red]Сетевая ошибка: {exc}[/red]")
            raise typer.Exit(code=1)

        try:
            payload = resp.json()
        except Exception:
            payload = {"detail": resp.text[:300]}

        if resp.is_success:
            replaced = bool(payload.get("replaced")) or force
            dl_url = payload.get("url", "")
            console.print(
                f"[green]✓[/green] [bold]{name}[/bold] v{version} "
                f"{'заменён' if replaced else 'опубликован'} в канале [bold]{channel}[/bold]"
            )
            if dl_url:
                console.print(f"[dim]URL: {dl_url}[/dim]")
        else:
            detail = payload.get("detail") or payload
            if resp.status_code == 403:
                console.print(
                    "[red]403 Forbidden:[/red] у токена нет прав на этот плагин, "
                    "или MARKETPLACE_ALLOW_RAW_UPLOAD=false на сервере"
                )
            elif resp.status_code == 409:
                console.print(
                    f"[red]409 Conflict:[/red] версия {version} уже существует. "
                    "Используй --force для замены."
                )
            else:
                console.print(f"[red]Ошибка {resp.status_code}:[/red] {detail}")
            raise typer.Exit(code=1)
