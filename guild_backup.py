"""Portable, guild-scoped snapshots of PRIME's persisted SQLite data."""

from __future__ import annotations

import base64
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

import database


BACKUP_FORMAT = "prime-discord-guild-backup"
BACKUP_VERSION = 1
MAX_BACKUP_BYTES = 64 * 1024 * 1024
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# These tables are scoped through their parent event rather than a guild_id
# column. Global streak stage definitions and personal dashboard themes are
# intentionally excluded from a per-server backup.
_CHILD_TABLES = {
    "giveaway_entries": ("giveaways", "giveaway_id", "id"),
    "scrim_registrations": ("scrim_configs", "scrim_id", "id"),
    "tournament_entries": ("tournaments", "tournament_id", "id"),
}


class GuildBackupError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _quote(name: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(name):
        raise GuildBackupError("اسم جدول غير صالح في ملف النسخة.")
    return f'"{name}"'


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [
        row[1]
        for row in conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
    ]


def _scope_tables(conn: sqlite3.Connection) -> dict[str, dict[str, str | None]]:
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    scoped: dict[str, dict[str, str | None]] = {}
    columns_by_table = {name: set(_table_columns(conn, name)) for name in names}

    for name, columns in columns_by_table.items():
        if "guild_id" in columns:
            scoped[name] = {
                "parent": None,
                "parent_key": None,
                "child_key": None,
            }

    for child, (parent, child_key, parent_key) in _CHILD_TABLES.items():
        if child not in names or parent not in names:
            continue
        if (
            child_key not in columns_by_table[child]
            or parent_key not in columns_by_table[parent]
            or "guild_id" not in columns_by_table[parent]
        ):
            raise GuildBackupError(
                "مخطط قاعدة البيانات لا يطابق علاقات النسخ الاحتياطي.",
                409,
            )
        scoped[child] = {
            "parent": parent,
            "parent_key": parent_key,
            "child_key": child_key,
        }

    return scoped


def _encode_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "$prime_backup_binary": base64.b64encode(value).decode("ascii"),
        }
    if value is None or isinstance(value, (str, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise GuildBackupError("تحتوي قاعدة البيانات على قيمة رقمية غير صالحة.", 409)
        return value
    raise GuildBackupError("تحتوي قاعدة البيانات على نوع بيانات لا يمكن نسخه.", 409)


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) != {"$prime_backup_binary"}:
            raise GuildBackupError("يحتوي ملف النسخة على قيمة غير صالحة.")
        encoded = value["$prime_backup_binary"]
        if not isinstance(encoded, str):
            raise GuildBackupError("يحتوي ملف النسخة على بيانات ثنائية غير صالحة.")
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise GuildBackupError("تعذر قراءة صورة أو ملف من النسخة.") from error
    if value is None or isinstance(value, (str, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise GuildBackupError("يحتوي ملف النسخة على قيمة رقمية غير صالحة.")
        return value
    raise GuildBackupError("يحتوي ملف النسخة على قيمة غير صالحة.")


def _open_database() -> sqlite3.Connection:
    path = database.ensure_db_directory(database.DB_NAME)
    conn = sqlite3.connect(path, timeout=database.DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {int(database.DB_TIMEOUT * 1000)}")
    return conn


def create_guild_backup(guild_id: int) -> tuple[bytes, dict[str, int | str]]:
    """Return a consistent JSON snapshot containing only one guild's bot data."""
    conn = _open_database()
    try:
        conn.execute("BEGIN")
        scoped = _scope_tables(conn)
        tables: dict[str, dict[str, Any]] = {}
        total_rows = 0

        for table in sorted(scoped):
            columns = _table_columns(conn, table)
            relation = scoped[table]
            if relation["parent"] is None:
                rows = conn.execute(
                    f"SELECT * FROM {_quote(table)} WHERE guild_id = ?",
                    (guild_id,),
                ).fetchall()
            else:
                parent = str(relation["parent"])
                parent_key = str(relation["parent_key"])
                child_key = str(relation["child_key"])
                rows = conn.execute(
                    f"SELECT child.* FROM {_quote(table)} AS child "
                    f"JOIN {_quote(parent)} AS parent "
                    f"ON child.{_quote(child_key)} = parent.{_quote(parent_key)} "
                    "WHERE parent.guild_id = ?",
                    (guild_id,),
                ).fetchall()
            tables[table] = {
                "columns": columns,
                "rows": [
                    [_encode_value(row[column]) for column in columns]
                    for row in rows
                ],
            }
            total_rows += len(rows)

        backup = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "guild_id": str(guild_id),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tables": tables,
        }
        body = json.dumps(
            backup,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > MAX_BACKUP_BYTES:
            raise GuildBackupError(
                "حجم النسخة يتجاوز الحد الآمن للتنزيل؛ لم يُنشأ ملف جزئي.",
                413,
            )
        return body, {
            "guild_id": str(guild_id),
            "table_count": len(tables),
            "row_count": total_rows,
            "created_at": backup["created_at"],
        }
    finally:
        conn.close()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GuildBackupError("ملف النسخة يحتوي على حقول مكررة.")
        result[key] = value
    return result


def _foreign_key_order(
    conn: sqlite3.Connection,
    tables: set[str],
    scoped: dict[str, dict[str, str | None]],
) -> list[str]:
    dependencies: dict[str, set[str]] = {table: set() for table in tables}
    for child in tables:
        for row in conn.execute(f"PRAGMA foreign_key_list({_quote(child)})").fetchall():
            parent = row[2]
            if parent in tables and parent != child:
                dependencies[child].add(parent)
    for child, relation in scoped.items():
        parent = relation["parent"]
        if child in tables and parent in tables and parent:
            dependencies[child].add(parent)

    remaining = set(tables)
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            table
            for table in remaining
            if not (dependencies[table] & remaining)
        )
        if not ready:
            raise GuildBackupError(
                "تحتوي قاعدة البيانات على علاقة دائرية تمنع استعادة آمنة.",
                409,
            )
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered


def _parse_backup(raw: bytes | str, guild_id: int) -> dict[str, Any]:
    if isinstance(raw, bytes):
        if len(raw) > MAX_BACKUP_BYTES:
            raise GuildBackupError("حجم ملف النسخة أكبر من الحد المسموح.", 413)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise GuildBackupError("ملف النسخة ليس نص JSON صالحاً.") from error
    elif isinstance(raw, str):
        text = raw
        if len(text.encode("utf-8")) > MAX_BACKUP_BYTES:
            raise GuildBackupError("حجم ملف النسخة أكبر من الحد المسموح.", 413)
    else:
        raise GuildBackupError("ملف النسخة غير صالح.")

    try:
        backup = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except GuildBackupError:
        raise
    except (json.JSONDecodeError, UnicodeError) as error:
        raise GuildBackupError("تعذر قراءة ملف النسخة. اختر ملف JSON صالحاً.") from error

    if not isinstance(backup, dict):
        raise GuildBackupError("ملف النسخة غير صالح.")
    if backup.get("format") != BACKUP_FORMAT or backup.get("version") != BACKUP_VERSION:
        raise GuildBackupError(
            "صيغة النسخة غير مدعومة. استخدم ملفاً أُنشئ من لوحة PRIME.",
            409,
        )
    if backup.get("guild_id") != str(guild_id):
        raise GuildBackupError(
            "هذه النسخة تخص سيرفراً آخر؛ لم يتم تغيير أي بيانات.",
            409,
        )
    if not isinstance(backup.get("tables"), dict):
        raise GuildBackupError("ملف النسخة لا يحتوي على بيانات جداول صالحة.")
    return backup


def restore_guild_backup(guild_id: int, raw: bytes | str) -> dict[str, int | str]:
    """Atomically replace only the selected guild's persisted bot rows."""
    backup = _parse_backup(raw, guild_id)
    conn = _open_database()
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        scoped = _scope_tables(conn)
        source_tables = backup["tables"]

        if set(source_tables) != set(scoped):
            raise GuildBackupError(
                "مخطط النسخة مختلف عن قاعدة البيانات الحالية؛ لم يتم تغيير أي بيانات. "
                "أنشئ نسخة جديدة من إصدار PRIME الحالي.",
                409,
            )

        decoded: dict[str, list[tuple[Any, ...]]] = {}
        total_rows = 0
        for table in sorted(scoped):
            table_data = source_tables[table]
            if not isinstance(table_data, dict):
                raise GuildBackupError("بيانات أحد الجداول في النسخة غير صالحة.")
            columns = _table_columns(conn, table)
            if table_data.get("columns") != columns:
                raise GuildBackupError(
                    "أعمدة النسخة لا تطابق قاعدة البيانات الحالية؛ لم يتم تغيير أي بيانات.",
                    409,
                )
            rows = table_data.get("rows")
            if not isinstance(rows, list):
                raise GuildBackupError("صفوف أحد الجداول في النسخة غير صالحة.")

            decoded_rows = []
            for row in rows:
                if not isinstance(row, list) or len(row) != len(columns):
                    raise GuildBackupError("أحد صفوف النسخة غير صالح.")
                values = tuple(_decode_value(value) for value in row)
                relation = scoped[table]
                if relation["parent"] is None:
                    guild_value = values[columns.index("guild_id")]
                    if isinstance(guild_value, bool) or str(guild_value) != str(guild_id):
                        raise GuildBackupError(
                            "تحتوي النسخة على بيانات لسيرفر آخر؛ لم يتم تغيير أي بيانات.",
                            409,
                        )
                decoded_rows.append(values)
            decoded[table] = decoded_rows
            total_rows += len(decoded_rows)

        # Ensure rows without guild_id still belong to parent events in this file.
        for child, relation in scoped.items():
            parent = relation["parent"]
            if parent is None:
                continue
            parent_columns = _table_columns(conn, str(parent))
            child_columns = _table_columns(conn, child)
            parent_index = parent_columns.index(str(relation["parent_key"]))
            child_index = child_columns.index(str(relation["child_key"]))
            parent_ids = {row[parent_index] for row in decoded[str(parent)]}
            if any(
                row[child_index] not in parent_ids
                for row in decoded[child]
            ):
                raise GuildBackupError(
                    "تحتوي النسخة على تسجيل غير مرتبط ببيانات السيرفر.",
                    409,
                )

        ordered = _foreign_key_order(conn, set(scoped), scoped)

        # Remove related event entries before deleting their parent rows.
        for child, relation in scoped.items():
            parent = relation["parent"]
            if parent is None:
                continue
            conn.execute(
                f"DELETE FROM {_quote(child)} "
                f"WHERE {_quote(str(relation['child_key']))} IN ("
                f"SELECT {_quote(str(relation['parent_key']))} "
                f"FROM {_quote(str(parent))} WHERE guild_id = ?)",
                (guild_id,),
            )

        # Child tables with declared foreign keys are deleted before parents.
        for table in reversed(ordered):
            if scoped[table]["parent"] is None:
                conn.execute(
                    f"DELETE FROM {_quote(table)} WHERE guild_id = ?",
                    (guild_id,),
                )

        for table in ordered:
            columns = _table_columns(conn, table)
            if not decoded[table]:
                continue
            column_sql = ", ".join(_quote(column) for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            conn.executemany(
                f"INSERT INTO {_quote(table)} ({column_sql}) "
                f"VALUES ({placeholders})",
                decoded[table],
            )

        conn.commit()
        database.invalidate_guild_settings(guild_id)
        database._stats_cache.pop(guild_id, None)
        database.invalidate_command_cache(guild_id)
        database.LOG_ROUTING_CACHE.pop(guild_id, None)
        database.LOG_CATEGORY_SETTINGS_CACHE.pop(guild_id, None)
        return {
            "guild_id": str(guild_id),
            "table_count": len(scoped),
            "row_count": total_rows,
            "created_at": str(backup.get("created_at", "")),
        }
    except GuildBackupError:
        conn.rollback()
        raise
    except sqlite3.IntegrityError as error:
        conn.rollback()
        raise GuildBackupError(
            "تعذر الاستعادة بسبب تعارض في السجلات أو اختلاف مخطط البيانات. "
            "لم يتم تغيير أي بيانات.",
            409,
        ) from error
    except sqlite3.Error as error:
        conn.rollback()
        raise GuildBackupError(
            "تعذر استعادة النسخة من قاعدة البيانات؛ لم يتم اعتماد أي تغيير.",
            409,
        ) from error
    finally:
        conn.close()
