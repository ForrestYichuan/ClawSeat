"""_toml_compat — vendored TOML compatibility shim.

Provides :func:`loads_safe` and :func:`load_safe` that work when neither
``tomllib`` (Python 3.11+) nor ``tomli`` (third-party) is installed. A
small pure-Python fallback is used in that case, covering the TOML shapes
ClawSeat runtime config actually reads: scalars, arrays, dotted tables,
quoted table keys, inline tables, and simple ``[[array-of-table]]`` blocks.

Import this module instead of importing ``tomllib`` / ``tomli`` directly so
the desktop Electron runtime does not crash when it ships a Python build
without these packages.

Usage::

    from _toml_compat import loads_safe, load_safe

    data = loads_safe(path.read_text(encoding="utf-8"))
    # or for binary files:
    with path.open("rb") as fh:
        data = load_safe(fh)
"""
from __future__ import annotations

import json
import re
from typing import IO, Any


def _toml_module() -> Any:
    """Return the first available TOML parser module or None."""
    try:
        import tomllib as _m  # type: ignore[import-not-found]
        return _m
    except ModuleNotFoundError:
        pass
    try:
        import tomli as _m  # type: ignore[import-not-found,no-redef]
        return _m
    except ModuleNotFoundError:
        pass
    return None


def _strip_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None:
            return line[:index]
    return line


def _balanced(text: str) -> bool:
    quote: str | None = None
    escaped = False
    square = 0
    curly = 0
    for char in text:
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if quote is not None:
            continue
        if char == "[":
            square += 1
        elif char == "]":
            square -= 1
        elif char == "{":
            curly += 1
        elif char == "}":
            curly -= 1
    return quote is None and square == 0 and curly == 0


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        pending = f"{pending} {line}".strip() if pending else line
        if _balanced(pending):
            lines.append(pending)
            pending = ""
    if pending:
        lines.append(pending)
    return lines


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    parts: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    square = 0
    curly = 0
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if quote is not None:
            continue
        if char == "[":
            square += 1
        elif char == "]":
            square -= 1
        elif char == "{":
            curly += 1
        elif char == "}":
            curly -= 1
        elif char == separator and square == 0 and curly == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return parts


def _split_key_value(line: str) -> tuple[str, str] | None:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "=" and quote is None:
            return line[:index].strip(), line[index + 1 :].strip()
    return None


def _parse_string(value: str) -> str:
    if value.startswith('"') and value.endswith('"'):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, str):
                return parsed
        except Exception:
            pass
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def _parse_key_path(raw: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for char in raw.strip():
        if escaped:
            current.append(char)
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
                continue
            if quote == char:
                quote = None
                continue
        if char == "." and quote is None:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)
    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def _ensure_table(root: dict[str, Any], path: list[str]) -> dict[str, Any]:
    current = root
    for part in path:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    return current


def _assign_value(root: dict[str, Any], path: list[str], value: Any) -> None:
    if not path:
        return
    target = _ensure_table(root, path[:-1])
    target[path[-1]] = value


def _parse_array(value: str) -> list[Any]:
    inner = value[1:-1].strip()
    if not inner:
        return []
    return [_parse_value(part) for part in _split_top_level(inner) if part]


def _parse_inline_table(value: str) -> dict[str, Any]:
    inner = value[1:-1].strip()
    table: dict[str, Any] = {}
    if not inner:
        return table
    for part in _split_top_level(inner):
        split = _split_key_value(part)
        if split is None:
            continue
        key, raw_value = split
        _assign_value(table, _parse_key_path(key), _parse_value(raw_value))
    return table


def _parse_value(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return _parse_string(value)
    if value.startswith("[") and value.endswith("]"):
        return _parse_array(value)
    if value.startswith("{") and value.endswith("}"):
        return _parse_inline_table(value)
    if value == "true":
        return True
    if value == "false":
        return False
    if re.match(r"^-?\d+$", value):
        try:
            return int(value)
        except ValueError:
            return value
    if re.match(r"^-?(?:\d+\.\d*|\d*\.\d+)$", value):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _fallback_loads(text: str) -> dict[str, Any]:
    """Small TOML parser for ClawSeat runtime config when no parser is installed."""
    result: dict[str, Any] = {}
    current_section = result
    for line in _logical_lines(text):
        array_table_m = re.match(r"^\[\[([^\[\]]+)\]\]$", line)
        if array_table_m:
            path = _parse_key_path(array_table_m.group(1))
            parent = _ensure_table(result, path[:-1])
            key = path[-1]
            existing = parent.get(key)
            if not isinstance(existing, list):
                existing = []
                parent[key] = existing
            item: dict[str, Any] = {}
            existing.append(item)
            current_section = item
            continue

        table_m = re.match(r"^\[([^\[\]]+)\]$", line)
        if table_m:
            current_section = _ensure_table(result, _parse_key_path(table_m.group(1)))
            continue

        split = _split_key_value(line)
        if split is None:
            continue
        key, raw_value = split
        _assign_value(current_section, _parse_key_path(key), _parse_value(raw_value))
    return result


def loads_safe(text: str) -> dict[str, Any]:
    """Parse a TOML string, falling back to a regex parser when tomllib/tomli unavailable."""
    mod = _toml_module()
    if mod is not None:
        try:
            return mod.loads(text)  # type: ignore[no-any-return]
        except Exception:
            return {}
    try:
        return _fallback_loads(text)
    except Exception:
        return {}


def load_safe(fh: IO[bytes]) -> dict[str, Any]:
    """Parse a binary TOML file-like object, with fallback."""
    mod = _toml_module()
    if mod is not None:
        try:
            return mod.load(fh)  # type: ignore[no-any-return]
        except Exception:
            return {}
    try:
        text = fh.read().decode('utf-8', errors='replace')
    except Exception:
        return {}
    try:
        return _fallback_loads(text)
    except Exception:
        return {}
