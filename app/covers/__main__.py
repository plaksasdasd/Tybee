"""CLI: ``python -m app.covers {list,add,check,enable,disable,init-default,upgrade}``.

Works directly with the database; a running process picks the changes up on its
own (see the supervisor). Tokens are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.covers import loader, service
from app.db import base as db_base
from app.db.models import Cover


async def _get(session, slug: str) -> Cover | None:
    return await session.scalar(select(Cover).where(Cover.slug == slug))


def _print_errors(errors: list[str]) -> None:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)


async def cmd_list(_args) -> int:
    async with db_base.session_factory() as session:
        covers = list(await session.scalars(select(Cover).order_by(Cover.id)))
    rows = [("slug", "enabled", "running", "bot_username", "path", "last_error")]
    for c in covers:
        running = service.is_running(c)
        rows.append(
            (
                c.slug,
                "yes" if c.enabled else "no",
                "yes" if running else "no",
                f"@{c.bot_username}" if c.bot_username else "-",
                c.path,
                (c.last_error or "-").replace("\n", " "),
            )
        )
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(str(v).ljust(widths[i]) for i, v in enumerate(row)).rstrip())
    return 0


async def cmd_add(args) -> int:
    slug = args.slug
    if not loader.SLUG_RE.match(slug):
        _print_errors([f"slug '{slug}': допустимы a-z, 0-9, _ и -, длина 1..32"])
        return 1
    folder = loader.resolve_path(args.path)
    errors = loader.validate_cover_folder(folder)
    async with db_base.session_factory() as session:
        if await _get(session, slug) is not None:
            errors.append(f"slug '{slug}' уже существует")
        if not errors:
            token = loader.get_config_from_folder(folder)["token"]
            owner = await loader.find_token_owner(session, token)
            if owner is not None:
                errors.append(
                    f"токен бота {loader.mask_token(token)} уже используется обложкой '{owner}'"
                )
        if errors:
            _print_errors(errors)
            return 1
        session.add(Cover(slug=slug, path=str(folder.resolve()), enabled=True))
        await session.commit()
    print(f"OK added {slug}")
    return 0


async def cmd_check(args) -> int:
    async with db_base.session_factory() as session:
        cover = await _get(session, args.slug)
        if cover is None:
            _print_errors([f"обложка '{args.slug}' не найдена"])
            return 1
        folder = loader.resolve_path(cover.path)
        errors = loader.validate_cover_folder(folder)
        if not errors:
            token = loader.get_config_from_folder(folder)["token"]
            owner = await loader.find_token_owner(session, token, exclude_cover_id=cover.id)
            if owner is not None:
                errors.append(
                    f"токен бота {loader.mask_token(token)} уже используется обложкой '{owner}'"
                )
    if errors:
        _print_errors(errors)
        return 1
    print(f"OK {args.slug}")
    return 0


async def _set_enabled(slug: str, enabled: bool) -> int:
    async with db_base.session_factory() as session:
        try:
            await service.set_enabled(session, slug, enabled)
        except service.CoverError as exc:
            _print_errors(exc.errors)
            return 1
    print(f"OK {slug} {'enabled' if enabled else 'disabled'}")
    return 0


async def cmd_enable(args) -> int:
    return await _set_enabled(args.slug, True)


async def cmd_disable(args) -> int:
    return await _set_enabled(args.slug, False)


async def cmd_init_default(_args) -> int:
    try:
        created = loader.init_default_cover()
    except loader.CoverConfigError as exc:
        _print_errors([str(exc)])
        return 1
    print("created" if created else "exists (nothing changed)")
    return 0


async def cmd_upgrade(_args) -> int:
    async with db_base.session_factory() as session:
        changed = await service.upgrade_all_covers(session)
    if not changed:
        print("all covers are up to date")
    for slug, keys in changed.items():
        print(f"{slug}: added {', '.join(keys)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.covers")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    add = sub.add_parser("add")
    add.add_argument("slug")
    add.add_argument("path")
    add.set_defaults(func=cmd_add)
    for name, func in (("check", cmd_check), ("enable", cmd_enable), ("disable", cmd_disable)):
        p = sub.add_parser(name)
        p.add_argument("slug")
        p.set_defaults(func=func)
    sub.add_parser("init-default").set_defaults(func=cmd_init_default)
    sub.add_parser("upgrade", help="add missing statuses/texts keys from the template").set_defaults(func=cmd_upgrade)
    return parser


async def _main(argv: list[str] | None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "init-default":
        await db_base.init_models()
    try:
        return await args.func(args)
    finally:
        await db_base.engine.dispose()


def main(argv: list[str] | None = None) -> None:
    sys.exit(asyncio.run(_main(argv)))


if __name__ == "__main__":
    main()
