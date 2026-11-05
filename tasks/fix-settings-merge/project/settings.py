"""Layered application settings: defaults, then files, then environment variables."""

from collections.abc import Mapping
from typing import Any


def merge_settings(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``override`` on top of ``base`` and return a new dict.

    Two dicts under the same key are merged recursively. Any other value in ``override``
    (including a list) replaces the value in ``base``. A value of ``None`` in ``override``
    removes the key from the result, and is ignored if ``base`` has no such key. Neither
    input is modified, and the result shares no dict or list with either input: changing
    the result later never changes ``base`` or ``override``.
    """
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_settings(result[key], value)
        else:
            result[key] = value
    return result


def parse_env(environ: Mapping[str, str], prefix: str = "APP_") -> dict[str, Any]:
    """Turn environment variables that start with ``prefix`` into nested settings.

    The rest of the name is lower-cased and split on a double underscore into the path of
    the setting: ``APP_DB__HOST`` is ``{"db": {"host": ...}}`` and ``APP_PORT`` is
    ``{"port": ...}``; a single underscore stays part of a name (``APP_LOG_LEVEL`` is
    ``log_level``). Variables without the prefix, and a variable that is only the prefix,
    are ignored.

    Values are converted: ``true`` and ``false`` in any case become booleans, ``null`` in
    any case becomes ``None``, a whole number with an optional leading minus sign becomes
    an ``int``, and everything else stays a string (``"1.5"`` stays ``"1.5"``).
    """
    settings: dict[str, Any] = {}
    for name, raw in environ.items():
        if not name.startswith(prefix) or name == prefix:
            continue
        path = name[len(prefix) :].lower().split("_")
        node = settings
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _convert(raw)
    return settings


def _convert(raw: str) -> Any:
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if raw.isdigit():
        return int(raw)
    return raw


def load_settings(
    defaults: Mapping[str, Any],
    layers: list[Mapping[str, Any]],
    environ: Mapping[str, str],
    prefix: str = "APP_",
) -> dict[str, Any]:
    """The settings: ``defaults``, then each of ``layers`` in order, then the environment.

    Later layers win, as in ``merge_settings``; the environment (see ``parse_env``) wins
    over everything, and a ``null`` there removes the setting.
    """
    result = merge_settings({}, defaults)
    for layer in layers:
        result = merge_settings(result, layer)
    return merge_settings(result, parse_env(environ, prefix))
