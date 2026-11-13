#!/usr/bin/env python3
"""Dry-run by default. Stop bot/site before --delete; this removes ALL support files."""
import argparse
import os
from pathlib import Path

from app.config import get_settings

parser = argparse.ArgumentParser(description="Удаление всех файлов DATA_DIR/attachments; сначала остановите сервисы.")
parser.add_argument("--delete", action="store_true", help="действительно удалить найденные файлы")
args = parser.parse_args()
root = (Path(get_settings().data_dir).resolve() / "attachments")
if root.is_symlink():
    raise SystemExit("attachments является символьной ссылкой: удаление отменено")
if not root.exists():
    print("Папка вложений отсутствует; удалять нечего.")
    raise SystemExit(0)
count = errors = deleted = 0
for directory, dirs, names in os.walk(root, followlinks=False):
    symlink_dirs = [d for d in dirs if (Path(directory) / d).is_symlink()]
    errors += len(symlink_dirs)
    dirs[:] = [d for d in dirs if d not in symlink_dirs]
    for name in names:
        path = Path(directory) / name
        if path.is_symlink() or not path.is_file():
            errors += 1
            continue
        count += 1
        if args.delete:
            try:
                path.unlink()
                deleted += 1
            except OSError:
                errors += 1
print(f"{'Удалено' if args.delete else 'Найдено'} файлов: {deleted if args.delete else count}; ошибок/пропущено: {errors}")
if not args.delete:
    print("Проверка без удаления. Для удаления используйте --delete при остановленных сервисах.")
if errors:
    raise SystemExit(1)
