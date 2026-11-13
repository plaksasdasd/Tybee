"""Cover management shared by the CLI (``python -m app.covers``) and the web panel.

All writes to a cover folder are atomic (tmp file + ``os.replace``), so the
bot process, which hot-reloads by ``mtime``, never sees a half-written file.
Tokens are only ever shown through :func:`loader.mask_token`.
"""
from __future__ import annotations

import copy
import logging
import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.covers import loader, spec
from app.db.models import Cover, Order, UserCover

logger = logging.getLogger(__name__)
_UPGRADE_SECTIONS = ("statuses", "texts")


class CoverError(Exception):
    """A user-facing error (Russian message). ``errors`` lists validator hits."""

    def __init__(self, message: str, errors: list[str] | None = None, status: int = 400) -> None:
        super().__init__(message)
        self.errors = errors or [message]
        self.status = status


async def get_cover(session: AsyncSession, slug: str) -> Cover:
    cover = await session.scalar(select(Cover).where(Cover.slug == slug, Cover.archived.is_(False)))
    if cover is None:
        raise CoverError(f"обложка '{slug}' не найдена", status=404)
    return cover


def cover_folder(cover: Cover) -> Path:
    return loader.resolve_path(cover.path)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


async def _check_token_free(session: AsyncSession, token: str, exclude_cover_id: int | None = None) -> None:
    from app.login_bot_config import token as login_bot_token
    login_token = login_bot_token().strip()
    if login_token and token.split(":", 1)[0] == login_token.split(":", 1)[0]:
        raise CoverError("Этот бот используется для входа в панель", status=409)
    owner = await loader.find_token_owner(session, token, exclude_cover_id=exclude_cover_id)
    if owner is not None:
        raise CoverError(f"токен бота {loader.mask_token(token)} уже используется обложкой '{owner}'")


async def create_cover(
    session: AsyncSession, slug: str, token: str, brand_name: str, template_slug: str = "default"
) -> Cover:
    if not loader.SLUG_RE.match(slug):
        raise CoverError(f"slug '{slug}': допустимы a-z, 0-9, _ и -, длина 1..32")
    if await session.scalar(select(Cover).where(Cover.slug == slug)):
        raise CoverError(f"slug '{slug}' уже существует")
    token = token.strip()
    brand_name = brand_name.strip()
    root = Path(get_settings().covers_root)
    if not loader.SLUG_RE.match(template_slug):
        raise CoverError("неверное имя шаблона")
    template = root / template_slug
    if not template.is_dir():
        raise CoverError(f"шаблон '{template_slug}' не найден")
    target = root / slug
    if target.exists():
        raise CoverError(f"папка {target.name} уже существует")
    await _check_token_free(session, token)

    try:
        shutil.copytree(template, target, ignore=shutil.ignore_patterns(".*", "*.tmp"))
        data = loader.get_config_from_folder(target)
        data["token"] = token
        data["brand_name"] = brand_name
        errors = loader.validate_config_data(data)
        if errors:
            raise CoverError("конфигурация не прошла проверку", errors, status=422)
        _atomic_write(target / "config.yaml", loader.dump_config(data).encode("utf-8"))
        errors = loader.validate_cover_folder(target)
        if errors:
            raise CoverError("обложка не прошла проверку", errors, status=422)
        cover = Cover(slug=slug, path=str(target.resolve()), enabled=True)
        session.add(cover)
        await session.commit()
    except BaseException:
        await session.rollback()
        shutil.rmtree(target, ignore_errors=True)
        raise
    return cover


async def set_enabled(session: AsyncSession, slug: str, enabled: bool) -> Cover:
    cover = await get_cover(session, slug)
    cover.enabled = enabled
    await session.commit()
    return cover


def read_config(cover: Cover) -> dict[str, Any]:
    try:
        return loader.get_config_from_folder(cover_folder(cover))
    except loader.CoverConfigError as exc:
        raise CoverError(str(exc), status=500) from exc


def public_config(cover: Cover) -> dict[str, Any]:
    """Config for the UI: the token is replaced by its masked form."""
    data = copy.deepcopy(read_config(cover))
    token = data.pop("token", "")
    data["token_masked"] = loader.mask_token(token)
    return data


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if key in ("texts", "buttons", "statuses") and isinstance(value, dict):
            section = result.get(key)
            result[key] = {**(section if isinstance(section, dict) else {}), **value}
        else:
            result[key] = value
    return result


async def write_config(session: AsyncSession, cover: Cover, patch: dict[str, Any]) -> list[str]:
    """Merge ``patch`` into the cover config and save it atomically.

    Returns the names of the changed top-level fields / keys (never values).
    Raises :class:`CoverError` (status 422) with the validator's messages and
    leaves the file untouched when the result is invalid.
    """
    current = read_config(cover)
    merged = _merge(current, patch)
    errors = loader.validate_config_data(merged)
    if errors:
        raise CoverError("конфигурация не прошла проверку", errors, status=422)
    if merged["token"] != current.get("token"):
        await _check_token_free(session, merged["token"], exclude_cover_id=cover.id)
    changed: list[str] = []
    for key, value in merged.items():
        old = current.get(key)
        if isinstance(value, dict) and isinstance(old, dict):
            changed.extend(f"{key}.{k}" for k in value if old.get(k) != value[k])
        elif old != value:
            changed.append(key)
    if changed:
        _atomic_write(cover_folder(cover) / "config.yaml", loader.dump_config(merged).encode("utf-8"))
    return changed


def banner_path(cover: Cover, name: str) -> Path:
    if name not in spec.BANNERS:
        raise CoverError(f"неизвестный баннер '{name}'", status=404)
    path = cover_folder(cover) / "banners" / f"{name}.png"
    if not path.is_file():
        raise CoverError(f"banners/{name}.png: файл не найден", status=404)
    return path


def replace_banner(cover: Cover, name: str, data: bytes) -> list[str]:
    if name not in spec.BANNERS:
        raise CoverError(f"неизвестный баннер '{name}'", status=404)
    if not data:
        raise CoverError("пустой файл", status=422)
    if len(data) > spec.BANNER_MAX_BYTES:
        raise CoverError("размер больше 10 МБ", status=422)
    from app.support.attachments import validate_image
    if validate_image(data) != "image/png":
        raise CoverError("Некорректное изображение PNG", status=422)
    _atomic_write(cover_folder(cover) / "banners" / f"{name}.png", data)
    return [f"banners/{name}"]


def banner_version(cover: Cover, name: str) -> str:
    try:
        return loader.banner_fingerprint(banner_path(cover, name)).replace(":", "-")
    except (CoverError, OSError):
        return "0"


async def cover_stats(session: AsyncSession) -> dict[int, dict[str, int]]:
    stats: dict[int, dict[str, int]] = {}
    clients = await session.execute(
        select(UserCover.cover_id, func.count()).group_by(UserCover.cover_id)
    )
    for cover_id, count in clients:
        stats.setdefault(cover_id, {"clients": 0, "orders": 0})["clients"] = count
    orders = await session.execute(
        select(Order.cover_id, func.count()).where(Order.cover_id.is_not(None)).group_by(Order.cover_id)
    )
    for cover_id, count in orders:
        stats.setdefault(cover_id, {"clients": 0, "orders": 0})["orders"] = count
    return stats


def is_running(cover: Cover) -> bool:
    return bool(cover.enabled and cover.bot_username and not cover.last_error)


def _template() -> dict[str, Any]:
    with open(Path(loader.__file__).with_name("default_config.yaml"), encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def upgrade_config_keys(folders: Iterable[str | os.PathLike[str]] | None = None) -> dict[str, list[str]]:
    """Add keys that appeared in the bundled template (``statuses``/``texts``) to existing covers.

    Only missing keys are written; existing values are never touched. The file is
    replaced atomically and only when something was added. ``folders`` defaults to
    every sub-folder of ``covers_root`` with a ``config.yaml``. Returns
    ``{folder name: [added keys]}`` for the folders that changed.
    """
    template = _template()
    if folders is None:
        root = Path(get_settings().covers_root)
        folders = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    seen: set[Path] = set()
    changed: dict[str, list[str]] = {}
    for item in folders:
        folder = Path(item).resolve()
        config_path = folder / "config.yaml"
        if folder in seen or not config_path.is_file():
            continue
        seen.add(folder)
        try:
            data = loader.get_config_from_folder(folder)
        except loader.CoverConfigError as exc:
            logger.warning("cover %s: config.yaml not upgraded: %s", folder.name, exc)
            continue
        added: list[str] = []
        for section in _UPGRADE_SECTIONS:
            current = data.get(section)
            if not isinstance(current, dict):
                continue
            for key, value in template[section].items():
                if key not in current:
                    current[key] = value
                    added.append(f"{section}.{key}")
        if added:
            _atomic_write(config_path, loader.dump_config(data).encode("utf-8"))
            logger.info("cover %s: config.yaml upgraded, added keys: %s", folder.name, ", ".join(added))
            changed[folder.name] = added
    return changed


async def upgrade_all_covers(session: AsyncSession) -> dict[str, list[str]]:
    """``upgrade_config_keys`` for every folder under covers_root and every cover in the DB."""
    root = Path(get_settings().covers_root)
    folders: list[Path] = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    for cover in await session.scalars(select(Cover).order_by(Cover.id)):
        folders.append(cover_folder(cover))
    return upgrade_config_keys(folders)
