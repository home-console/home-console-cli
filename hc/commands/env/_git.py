"""Git operations: pull, stash, fetch for env commands."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.console import Console

from hc.core_source import CoreSource


def _git(path: Path, *args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=str(path),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@dataclass(slots=True)
class PullResult:
    updated: bool
    changed_files: list[str] = field(default_factory=list)
    skipped: bool = False


def pull_git_repo(
    path: Path,
    console: Console,
    *,
    label: str,
    quiet: bool = False,
    autostash: bool = True,
) -> PullResult:
    """
    git pull репозитория в `path` с попыткой обычного merge, если `--ff-only`
    невозможен из-за разошедшихся веток.

    Если рабочее дерево не чистое: при autostash=True откладывает правки в
    stash и возвращает их после pull; при autostash=False — пропускает pull
    этого репозитория (PullResult.skipped=True).
    """
    status = _git(path, "status", "--porcelain", timeout=5)
    if status.returncode != 0:
        msg = f"{label}: не git-репозиторий или git недоступен."
        if quiet:
            return PullResult(updated=False)
        console.print(f"[red]Ошибка:[/red] {msg}")
        raise typer.Exit(code=1)

    stashed = False
    if status.stdout.strip():
        if not autostash:
            if not quiet:
                console.print(
                    f"[yellow]![/yellow] {label}: рабочее дерево не чистое — pull пропущен "
                    f"(вероятно, тут твои правки). Закоммить/stash и повтори вручную."
                )
            return PullResult(updated=False, skipped=True)

        stash = _git(path, "stash", "push", "-u", "-m", "hc env pull: autostash")
        if stash.returncode != 0:
            if quiet:
                return PullResult(updated=False)
            err = (stash.stderr or stash.stdout or "git stash failed").strip()
            console.print(f"[red]Ошибка git stash ({label}):[/red] {err}")
            raise typer.Exit(code=stash.returncode)
        stashed = True
        if not quiet:
            console.print(f"[cyan]→[/cyan] {label}: локальные правки отложены в stash")

    old_head = _git(path, "rev-parse", "HEAD", timeout=5).stdout.strip()

    pull = _git(path, "pull", "--ff-only")
    if pull.returncode != 0:
        err = (pull.stderr or pull.stdout or "").lower()
        if "not possible to fast-forward" in err or "would not be possible to fast-forward" in err:
            if not quiet:
                console.print(
                    f"[yellow]![/yellow] {label}: ветки разошлись, пробую обычный merge..."
                )
            pull = _git(path, "pull")
        if pull.returncode != 0:
            _restore_stash(path, console, label=label, quiet=quiet)
            if quiet:
                return PullResult(updated=False)
            err_out = (pull.stderr or pull.stdout or "git pull failed").strip()
            console.print(f"[red]Ошибка git pull ({label}):[/red] {err_out}")
            if "CONFLICT" in (pull.stdout or "") or "conflict" in err:
                console.print(
                    f"[dim]Резолви конфликты в {path}: правь файлы, `git add`, "
                    f"`git commit` (или `git merge --abort` для отката).[/dim]"
                )
            raise typer.Exit(code=pull.returncode)

    out = (pull.stdout or "").strip()
    updated = not ("Already up to date" in out or "Уже актуально" in out)

    changed_files: list[str] = []
    if updated:
        new_head = _git(path, "rev-parse", "HEAD", timeout=5).stdout.strip()
        diff = _git(path, "diff", "--name-only", old_head, new_head)
        changed_files = [f for f in (diff.stdout or "").splitlines() if f.strip()]

    if stashed:
        _restore_stash(path, console, label=label, quiet=quiet)

    if not updated:
        if not quiet:
            console.print(f"[green]✓[/green] {label} уже актуален")
        return PullResult(updated=False)

    if not quiet:
        console.print(f"[green]✓[/green] {label} обновлён")
        if out:
            for line in out.splitlines()[-3:]:
                console.print(f"  [dim]{line}[/dim]")
    return PullResult(updated=True, changed_files=changed_files)


def fetch_incoming_commits(path: Path, console: Console, *, label: str) -> list[str]:
    """git fetch + список входящих коммитов (для --dry-run), без изменения рабочего дерева."""
    fetch = _git(path, "fetch", timeout=60)
    if fetch.returncode != 0:
        err = (fetch.stderr or fetch.stdout or "git fetch failed").strip()
        console.print(f"[red]Ошибка git fetch ({label}):[/red] {err}")
        raise typer.Exit(code=fetch.returncode)

    upstream = _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=5)
    if upstream.returncode != 0:
        console.print(f"[yellow]![/yellow] {label}: нет upstream-ветки, пропускаю")
        return []

    log = _git(path, "log", "--oneline", f"HEAD..{upstream.stdout.strip()}")
    commits = [ln for ln in (log.stdout or "").splitlines() if ln.strip()]
    if commits:
        console.print(f"[cyan]→[/cyan] {label}: новых коммитов — {len(commits)}")
        for ln in commits[:10]:
            console.print(f"  [dim]{ln}[/dim]")
        if len(commits) > 10:
            console.print(f"  [dim]… и ещё {len(commits) - 10}[/dim]")
    else:
        console.print(f"[green]✓[/green] {label} уже актуален")
    return commits


def _restore_stash(path: Path, console: Console, *, label: str, quiet: bool) -> None:
    """Вернуть автостеш после pull. При конфликте оставляет stash и предупреждает."""
    stash_list = _git(path, "stash", "list")
    if "hc env pull: autostash" not in (stash_list.stdout or ""):
        return

    pop = _git(path, "stash", "pop")
    if pop.returncode != 0:
        console.print(
            f"[yellow]![/yellow] {label}: не удалось автоматически вернуть отложенные "
            f"правки (конфликт со свежим pull)."
        )
        console.print(
            f"[dim]Правки остались в stash. Резолви вручную: "
            f"cd {path} && git stash pop[/dim]"
        )
        return
    if not quiet:
        console.print(f"[green]✓[/green] {label}: локальные правки возвращены из stash")


def pull_core_source(src: CoreSource, console: Console, *, quiet: bool = False) -> PullResult:
    """git pull для core-runtime-service (см. pull_git_repo)."""
    return pull_git_repo(src.path, console, label="core-runtime-service", quiet=quiet)


def _try_pull_source(src: CoreSource, console: Console) -> None:
    """Тихий pull перед env up — ошибки не критичны."""
    try:
        pull_core_source(src, console, quiet=True)
    except typer.Exit:
        pass
