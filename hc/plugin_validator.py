"""Валидация структуры плагина HomeConsole.

Проверяет plugin.json по схеме и plugin.py через AST (без импорта кода плагина).
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Правила
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {"name", "version", "description", "class_path"}
NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")
ALLOWED_ROLES = {"capability_provider", "integration", "util", "core_extension"}

# Методы нового lifecycle API
NEW_LIFECYCLE = {"on_load", "on_start", "on_stop", "on_unload"}
# Методы старого API (deprecated)
OLD_LIFECYCLE = {"start", "stop"}


# ---------------------------------------------------------------------------
# Результат
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# plugin.json валидация
# ---------------------------------------------------------------------------

def _validate_manifest(path: Path, result: ValidationResult) -> dict | None:
    pj_path = path / "plugin.json"
    if not pj_path.is_file():
        result.errors.append("plugin.json не найден")
        return None

    try:
        data = json.loads(pj_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result.errors.append(f"plugin.json: невалидный JSON — {exc}")
        return None

    if not isinstance(data, dict):
        result.errors.append("plugin.json: ожидался object")
        return None

    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        result.errors.append(f"plugin.json: отсутствуют обязательные поля: {sorted(missing)}")

    name = data.get("name", "")
    if not isinstance(name, str) or not NAME_RE.match(name):
        result.errors.append(
            f"plugin.json.name='{name}' должно быть snake_case [a-z_][a-z0-9_]*"
        )
    elif name != path.name:
        result.errors.append(
            f"plugin.json.name='{name}' не совпадает с именем папки '{path.name}'"
        )

    version = data.get("version", "")
    if not isinstance(version, str) or not SEMVER_RE.match(version):
        result.errors.append(
            f"plugin.json.version='{version}' не является semver (например 0.1.0)"
        )

    role = data.get("role", "")
    if role and role not in ALLOWED_ROLES:
        result.errors.append(
            f"plugin.json.role='{role}' не из допустимых: {sorted(ALLOWED_ROLES)}"
        )

    class_path = data.get("class_path", "")
    resolved_module: Path | None = None
    if class_path:
        parts = class_path.split(".")
        if len(parts) < 2:
            result.errors.append(
                f"plugin.json.class_path='{class_path}' должен быть '<module>.<Class>'"
            )
        else:
            if parts[0] == "plugins":
                module_parts = parts[2:-1] if len(parts) >= 4 else []
            else:
                module_parts = parts[:-1]
            if module_parts:
                module_rel = Path(*module_parts).with_suffix(".py")
                resolved_module = path / module_rel
                if not resolved_module.is_file():
                    result.errors.append(
                        f"class_path указывает на отсутствующий файл: {resolved_module}"
                    )
                    resolved_module = None
            else:
                # Нет подмодуля — ищем plugin.py напрямую
                candidate = path / "plugin.py"
                if candidate.is_file():
                    resolved_module = candidate

    deps = data.get("dependencies", [])
    if not isinstance(deps, list):
        result.errors.append("plugin.json.dependencies должен быть массивом")

    return data


# ---------------------------------------------------------------------------
# AST-анализ plugin.py
# ---------------------------------------------------------------------------

def _find_class(tree: ast.Module, class_name: str) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    return None


def _base_names(cls: ast.ClassDef) -> list[str]:
    names: list[str] = []
    for base in cls.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _method_names(cls: ast.ClassDef) -> set[str]:
    methods: set[str] = set()
    for node in ast.walk(cls):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            methods.add(node.name)
    return methods


def _has_property(cls: ast.ClassDef, prop_name: str) -> bool:
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name != prop_name:
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == "property":
                return True
            if isinstance(decorator, ast.Attribute) and decorator.attr == "property":
                return True
    return False


def _validate_python(
    py_path: Path,
    class_name: str,
    result: ValidationResult,
) -> None:
    try:
        source = py_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        result.errors.append(f"Не удалось прочитать {py_path.name}: {exc}")
        return

    try:
        tree = ast.parse(source, filename=str(py_path))
    except SyntaxError as exc:
        result.errors.append(f"{py_path.name}: синтаксическая ошибка — {exc}")
        return

    cls = _find_class(tree, class_name)
    if cls is None:
        result.errors.append(
            f"{py_path.name}: класс '{class_name}' не найден"
        )
        return

    # Проверяем наследование от BasePlugin
    bases = _base_names(cls)
    if "BasePlugin" not in bases:
        if not bases:
            result.warnings.append(
                f"Класс '{class_name}' не наследует BasePlugin — рекомендуется "
                "наследование от sdk.plugin.BasePlugin"
            )
        else:
            result.warnings.append(
                f"Класс '{class_name}' наследует {bases}, но не BasePlugin"
            )

    methods = _method_names(cls)

    # Проверяем metadata property (только если унаследован BasePlugin)
    if "BasePlugin" in bases and not _has_property(cls, "metadata"):
        result.errors.append(
            f"Класс '{class_name}' наследует BasePlugin, но не реализует "
            "@property metadata: PluginMetadata"
        )

    # Deprecated: start/stop вместо on_start/on_stop
    using_old = OLD_LIFECYCLE & methods
    using_new = NEW_LIFECYCLE & methods
    if using_old and not using_new:
        result.warnings.append(
            f"Используются устаревшие методы {sorted(using_old)} — "
            "замени на on_start()/on_stop() для совместимости с BasePlugin"
        )

    # Синхронные on_start/on_stop (должны быть async)
    for node in ast.walk(cls):
        if isinstance(node, ast.FunctionDef) and node.name in NEW_LIFECYCLE:
            result.warnings.append(
                f"Метод '{node.name}' объявлен как синхронный — "
                "BasePlugin ожидает async def"
            )


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def validate_plugin(path: Path) -> ValidationResult:
    """Полная валидация плагина по пути к его папке."""
    result = ValidationResult()

    if not path.is_dir():
        result.errors.append(f"'{path}' не является папкой")
        return result

    data = _validate_manifest(path, result)
    if data is None:
        return result

    class_path = data.get("class_path", "")
    if class_path:
        parts = class_path.split(".")
        class_name = parts[-1] if parts else ""

        # Определяем путь к Python-файлу
        if parts[0] == "plugins":
            module_parts = parts[2:-1] if len(parts) >= 4 else []
        else:
            module_parts = parts[:-1]

        if module_parts:
            py_path = path / Path(*module_parts).with_suffix(".py")
        else:
            py_path = path / "plugin.py"

        if py_path.is_file() and class_name:
            _validate_python(py_path, class_name, result)
    else:
        # Нет class_path — проверяем plugin.py на всякий случай
        py_path = path / "plugin.py"
        if not py_path.is_file():
            result.warnings.append(
                "plugin.py не найден и class_path не задан — нечего проверять"
            )

    return result
