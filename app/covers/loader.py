"""Reading, validating and serving a cover's static content.

A cover folder is fully self-contained::

    <path>/config.yaml
    <path>/banners/<banner>.png

There is deliberately NO fallback between covers or to the code: a missing key
at runtime is an exception, never a silent default.

Parsed configs are cached by ``(path, mtime_ns)`` of ``config.yaml`` and
re-checked on every access, so edits are picked up without a restart. If a new
version is invalid, the previous valid one stays in use and the error is
remembered (see :func:`pop_runtime_errors`) so the supervisor can store it in
``covers.last_error``.
"""
from __future__ import annotations

import logging
import os
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.assets import Banner
from app.config import get_settings
from app.covers import spec

logger = logging.getLogger(__name__)

SLUG_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
_TOKEN_RE = re.compile(spec.TOKEN_RE)
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^<>]*)>")
_ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a",
    "code", "pre", "tg-spoiler", "blockquote",
}
_STUB = "x"


class CoverConfigError(RuntimeError):
    """The cover config is missing, invalid, or lacks a requested key."""


def mask_token(token: object) -> str:
    """Only the part before the colon is ever shown (bot id)."""
    text = str(token or "")
    return text.split(":", 1)[0] if ":" in text else "<token>"


# ---------------------------------------------------------------- yaml helpers
class _Dumper(yaml.SafeDumper):
    pass


def _represent_str(dumper: yaml.SafeDumper, value: str) -> Any:
    tag = "tag:yaml.org,2002:str"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        return dumper.represent_scalar(tag, value)
    if "\n" in value:
        return dumper.represent_scalar(tag, value, style="|")
    return dumper.represent_scalar(tag, value, style='"')


_Dumper.add_representer(str, _represent_str)


def dump_config(data: dict[str, Any]) -> str:
    return yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=10000)


# ------------------------------------------------------------------ validation
def _placeholders(template: str) -> set[str]:
    return {name.split(".")[0].split("[")[0] for _, name, _, _ in string.Formatter().parse(template) if name}


def _visible_length(text: str) -> int:
    import html

    return len(html.unescape(_TAG_RE.sub("", text)))


def _check_html(where: str, text: str) -> list[str]:
    errors: list[str] = []
    stack: list[str] = []
    for match in _TAG_RE.finditer(text):
        closing, name = match.group(1) == "/", match.group(2).lower()
        if name not in _ALLOWED_TAGS:
            errors.append(f"{where}: тег <{name}> не поддерживается Telegram")
            continue
        if closing:
            if not stack or stack[-1] != name:
                errors.append(f"{where}: тег </{name}> закрыт без пары или в неверном порядке")
            else:
                stack.pop()
        else:
            stack.append(name)
    for name in stack:
        errors.append(f"{where}: тег <{name}> не закрыт")
    return errors


def _validate_strings(
    section: str, values: Any, reference: dict[str, spec.KeySpec] | tuple[str, ...]
) -> list[str]:
    errors: list[str] = []
    if not isinstance(values, dict):
        return [f"config.yaml: '{section}' должен быть словарём"]
    expected = set(reference)
    for key in sorted(expected - set(values)):
        errors.append(f"config.yaml: {section}.{key}: ключ отсутствует")
    for key in sorted(set(values) - expected):
        errors.append(f"config.yaml: {section}.{key}: лишний ключ (опечатка?)")
    for key in sorted(expected & set(values)):
        where = f"config.yaml: {section}.{key}"
        value = values[key]
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{where}: значение должно быть непустой строкой")
            continue
        if section == "statuses":
            key_spec = spec.KeySpec(frozenset(), False)
        else:
            key_spec = reference[key]  # type: ignore[index]
        found = _placeholders(value) if _formattable(value) else set()
        if not _formattable(value):
            errors.append(f"{where}: ошибка в фигурных скобках формата")
            continue
        allowed = set(key_spec.placeholders) | {"brand"}
        extra = found - allowed
        missing = set(key_spec.placeholders) - found
        if extra:
            errors.append(f"{where}: недопустимые плейсхолдеры {sorted(extra)}")
        if missing:
            errors.append(f"{where}: нет обязательных плейсхолдеров {sorted(missing)}")
        if extra or missing:
            continue
        try:
            rendered = value.format(**{name: _STUB for name in allowed})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{where}: подстановка не удалась ({exc})")
            continue
        if key_spec.caption and _visible_length(rendered) > spec.CAPTION_LIMIT:
            errors.append(f"{where}: подпись длиннее {spec.CAPTION_LIMIT} символов")
        if section == "buttons" and len(rendered) > spec.BUTTON_LIMIT:
            errors.append(f"{where}: текст кнопки длиннее {spec.BUTTON_LIMIT} символов")
        errors.extend(_check_html(where, rendered))
    return errors


def _formattable(value: str) -> bool:
    try:
        list(string.Formatter().parse(value))
        return True
    except ValueError:
        return False


def _read_yaml(config_path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        with open(config_path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError:
        return None, [f"{config_path.name}: файл не найден"]
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
        return None, [f"{config_path.name}: не удалось прочитать ({type(exc).__name__}: {str(exc)[:200]})"]
    if not isinstance(data, dict):
        return None, [f"{config_path.name}: корень должен быть словарём"]
    return data, []


def validate_config_data(data: dict[str, Any], *, check_token_format: bool = True) -> list[str]:
    """V2–V7 on an already parsed config."""
    errors: list[str] = []
    expected = set(spec.TOP_LEVEL_KEYS)
    for key in sorted(expected - set(data)):
        errors.append(f"config.yaml: ключ верхнего уровня '{key}' отсутствует")
    for key in sorted(set(data) - expected):
        errors.append(f"config.yaml: лишний ключ верхнего уровня '{key}' (опечатка?)")

    if "token" in data:
        token = data["token"]
        if check_token_format and (not isinstance(token, str) or not _TOKEN_RE.match(token)):
            errors.append(
                f"config.yaml: token: неверный формат (бот {mask_token(token)})"
            )
        elif not check_token_format and (not isinstance(token, str) or not token.strip()):
            errors.append("config.yaml: token: должен быть непустой строкой")
    brand = data.get("brand_name")
    if "brand_name" in data and (not isinstance(brand, str) or not brand.strip()):
        errors.append("config.yaml: brand_name: должна быть непустая строка")
    if "unban_fine_rub" in data:
        fine = data["unban_fine_rub"]
        if not (fine == spec.SHARED or (isinstance(fine, int) and not isinstance(fine, bool) and fine > 0)):
            errors.append("config.yaml: unban_fine_rub: целое число > 0 или 'shared'")

    if "texts" in data:
        errors.extend(_validate_strings("texts", data["texts"], spec.TEXTS))
    if "buttons" in data:
        errors.extend(_validate_strings("buttons", data["buttons"], spec.BUTTONS))
    if "statuses" in data:
        errors.extend(_validate_strings("statuses", data["statuses"], spec.STATUSES))
    return errors


def _validate_banners(folder: Path) -> list[str]:
    errors: list[str] = []
    for name in spec.BANNERS:
        path = folder / "banners" / f"{name}.png"
        label = f"banners/{name}.png"
        try:
            size = path.stat().st_size
        except OSError:
            errors.append(f"{label}: файл не найден")
            continue
        if size <= 0:
            errors.append(f"{label}: пустой файл")
            continue
        if size > spec.BANNER_MAX_BYTES:
            errors.append(f"{label}: размер больше 10 МБ")
            continue
        try:
            with open(path, "rb") as handle:
                head = handle.read(len(spec.PNG_SIGNATURE))
        except OSError:
            errors.append(f"{label}: не удалось прочитать")
            continue
        if head != spec.PNG_SIGNATURE:
            errors.append(f"{label}: это не PNG (нет сигнатуры)")
    return errors


def validate_cover_folder(path: str | os.PathLike[str], *, check_token_format: bool = True) -> list[str]:
    """Validate a cover folder. Empty list = OK. Never prints a full token."""
    folder = Path(path)
    if not folder.is_dir():
        return [f"{folder}: папка не существует"]
    data, errors = _read_yaml(folder / "config.yaml")
    if data is None:
        return errors
    errors = validate_config_data(data, check_token_format=check_token_format)
    errors.extend(_validate_banners(folder))
    return errors


async def validate_all_covers(session: Any) -> list[str]:
    """Cross-cover checks: a token must not be used by two covers."""
    from sqlalchemy import select

    from app.db.models import Cover

    errors: list[str] = []
    seen: dict[str, str] = {}
    for cover in await session.scalars(select(Cover).order_by(Cover.id)):
        data, _ = _read_yaml(resolve_path(cover.path) / "config.yaml")
        token = data.get("token") if data else None
        if not isinstance(token, str):
            continue
        if token in seen:
            errors.append(
                f"токен бота {mask_token(token)} повторяется в обложках '{seen[token]}' и '{cover.slug}'"
            )
        else:
            seen[token] = cover.slug
    return errors


async def find_token_owner(session: Any, token: str, *, exclude_cover_id: int | None = None) -> str | None:
    """Slug of another cover that already uses ``token`` (compared by value)."""
    from sqlalchemy import select

    from app.db.models import Cover

    for cover in await session.scalars(select(Cover).order_by(Cover.id)):
        if cover.id == exclude_cover_id:
            continue
        data, _ = _read_yaml(resolve_path(cover.path) / "config.yaml")
        if data and data.get("token") == token:
            return cover.slug
    return None


# ----------------------------------------------------------------- path + cache
def resolve_path(path: str | os.PathLike[str]) -> Path:
    """Absolute paths are used as is. A relative one is looked up under
    ``covers_root`` first (``second``), then relative to the working directory
    (``./covers/second``); a bare name that exists nowhere belongs to
    ``covers_root``."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    under_root = Path(get_settings().covers_root) / candidate
    if under_root.exists():
        return under_root
    if candidate.exists() or len(candidate.parts) > 1 or str(path).startswith("."):
        return candidate
    return under_root


@dataclass(frozen=True)
class CoverConfig:
    token: str
    brand_name: str
    unban_fine_rub: Any
    texts: dict[str, str]
    buttons: dict[str, str]
    statuses: dict[str, str]


_paths: dict[int, Path] = {}
_cache: dict[Path, tuple[int, CoverConfig]] = {}
_errors: dict[int, str] = {}
_error_seen: dict[int, str] = {}


def set_cover_path(cover_id: int, path: str | os.PathLike[str]) -> None:
    _paths[cover_id] = resolve_path(path)


def forget_cover(cover_id: int) -> None:
    path = _paths.pop(cover_id, None)
    if path is not None and path not in _paths.values():
        _cache.pop(path, None)
    _errors.pop(cover_id, None)
    _error_seen.pop(cover_id, None)


def reset() -> None:
    """Drop all cached state (tests)."""
    _paths.clear()
    _cache.clear()
    _errors.clear()
    _error_seen.clear()


def pop_runtime_errors() -> dict[int, str | None]:
    """Hot-reload outcomes since the last call: cover_id -> error text, or
    None when a previously failing config became valid again."""
    result: dict[int, str | None] = dict(_errors)
    for cover_id in list(_error_seen):
        if cover_id not in _errors:
            result[cover_id] = None
    _error_seen.clear()
    _error_seen.update({cid: err for cid, err in _errors.items()})
    return result


def folder_of(cover_id: int) -> Path:
    try:
        return _paths[cover_id]
    except KeyError as exc:
        raise CoverConfigError(f"обложка {cover_id} не зарегистрирована") from exc


def _load(cover_id: int) -> CoverConfig:
    folder = folder_of(cover_id)
    config_path = folder / "config.yaml"
    try:
        mtime = config_path.stat().st_mtime_ns
    except OSError as exc:
        previous = _cache.get(folder)
        message = f"config.yaml: не найден ({exc.__class__.__name__})"
        if previous is not None:
            _record_error(cover_id, message)
            return previous[1]
        raise CoverConfigError(message) from exc

    cached = _cache.get(folder)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    data, errors = _read_yaml(config_path)
    if data is not None:
        errors = validate_config_data(data, check_token_format=False)
    if errors:
        message = "; ".join(errors[:5])
        if cached is not None:
            # Keep serving the last valid version.
            _cache[folder] = (mtime, cached[1])
            _record_error(cover_id, message)
            return cached[1]
        raise CoverConfigError(message)

    assert data is not None
    config = CoverConfig(
        token=data["token"],
        brand_name=data["brand_name"],
        unban_fine_rub=data["unban_fine_rub"],
        texts=dict(data["texts"]),
        buttons=dict(data["buttons"]),
        statuses=dict(data["statuses"]),
    )
    _cache[folder] = (mtime, config)
    _errors.pop(cover_id, None)
    return config


def _record_error(cover_id: int, message: str) -> None:
    if _errors.get(cover_id) != message:
        logger.error("cover_id=%s config.yaml reload rejected, keeping previous: %s", cover_id, message)
    _errors[cover_id] = message


def get_config(cover_id: int) -> CoverConfig:
    return _load(cover_id)


def cover_token(cover_id: int) -> str:
    return _load(cover_id).token


# ----------------------------------------------------------------------- access
def cover_text(cover_id: int, key: str, **fmt: Any) -> str:
    config = _load(cover_id)
    try:
        template = config.texts[key]
    except KeyError as exc:
        raise CoverConfigError(f"texts.{key}: ключ отсутствует") from exc
    return template.format(brand=config.brand_name, **fmt)


def cover_button(cover_id: int, key: str, **fmt: Any) -> str:
    config = _load(cover_id)
    try:
        template = config.buttons[key]
    except KeyError as exc:
        raise CoverConfigError(f"buttons.{key}: ключ отсутствует") from exc
    return template.format(brand=config.brand_name, **fmt)


def cover_status_title(cover_id: int, status: str) -> str:
    config = _load(cover_id)
    try:
        template = config.statuses[status]
    except KeyError as exc:
        raise CoverConfigError(f"statuses.{status}: ключ отсутствует") from exc
    return template.format(brand=config.brand_name)


def cover_brand(cover_id: int) -> str:
    return _load(cover_id).brand_name


def cover_unban_fine(cover_id: int) -> int:
    value = _load(cover_id).unban_fine_rub
    if value == spec.SHARED:
        return get_settings().unban_fine_rub
    return int(value)


def cover_banner_path(cover_id: int, banner: Banner | str) -> Path:
    name = banner.value if isinstance(banner, Banner) else banner
    path = folder_of(cover_id) / "banners" / f"{name}.png"
    if not path.is_file():
        raise CoverConfigError(f"banners/{name}.png: файл не найден")
    return path


def banner_fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


# ----------------------------------------------------------- default cover seed
_TEMPLATE = Path(__file__).with_name("default_config.yaml")
_REPO_BANNERS = Path(__file__).resolve().parent.parent.parent / "assets" / "banners"


def default_folder() -> Path:
    return Path(get_settings().covers_root) / "default"


def init_default_cover() -> bool:
    """Create ``<covers_root>/default`` from the bundled template if it has no
    config.yaml yet. Never overwrites an existing config or banner.

    Returns True when a new config.yaml was written.
    """
    import shutil

    folder = default_folder()
    config_path = folder / "config.yaml"
    created = False
    if not config_path.exists():
        token = get_settings().bot_token.strip()
        if not token:
            raise CoverConfigError(
                "covers/default/config.yaml отсутствует, а TYBEE_BOT_TOKEN не задан — "
                "создать обложку default нельзя"
            )
        with open(_TEMPLATE, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        data["token"] = token
        folder.mkdir(parents=True, exist_ok=True)
        config_path.write_text(dump_config(data), encoding="utf-8")
        created = True
    banners = folder / "banners"
    banners.mkdir(parents=True, exist_ok=True)
    for banner in Banner:
        target = banners / f"{banner.value}.png"
        source = _REPO_BANNERS / f"{banner.value}.png"
        if not target.exists() and source.is_file():
            shutil.copyfile(source, target)
    return created


def get_config_from_folder(folder: Path) -> dict[str, Any]:
    """Parsed config.yaml of a folder (caller has validated it)."""
    data, errors = _read_yaml(folder / "config.yaml")
    if data is None:
        raise CoverConfigError("; ".join(errors))
    return data
