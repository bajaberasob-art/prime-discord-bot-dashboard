"""Durable shared game-session engine backed by PRIME's existing database."""
from __future__ import annotations

import json
import logging
from typing import Any

import aiosqlite

from database import connect
from prime_games.registry import GameTransition, registry

logger = logging.getLogger("PrimeGames")


async def ensure_game_schema() -> None:
    """Create additive game tables in PRIME's existing SQLite database."""
    async with connect() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prime_game_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL DEFAULT 0,
                game_key TEXT NOT NULL,
                players_json TEXT NOT NULL,
                state_json TEXT NOT NULL,
                current_player_id INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                winner_id INTEGER,
                result TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_prime_games_guild_status "
            "ON prime_game_sessions(guild_id, status, id DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_prime_games_message "
            "ON prime_game_sessions(message_id)"
        )
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prime_game_reward_claims (
                session_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(session_id, user_id)
            )
        """)
        # A crash after creating a session but before sending its Discord message
        # should not leave an invisible active game that can never be resumed.
        await db.execute("""
            UPDATE prime_game_sessions
            SET status = 'cancelled',
                result = 'interrupted_before_publish',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'active' AND message_id = 0
        """)
        await db.commit()


def _session(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    try:
        players = json.loads(result.pop("players_json") or "[]")
        state = json.loads(result.pop("state_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        players, state = [], {}
    result["players"] = [int(value) for value in players if str(value).isdigit()]
    result["state"] = state if isinstance(state, dict) else {}
    result["guild_id"] = int(result["guild_id"])
    result["channel_id"] = int(result["channel_id"])
    result["message_id"] = int(result.get("message_id") or 0)
    result["current_player_id"] = (
        int(result["current_player_id"])
        if result.get("current_player_id") is not None else None
    )
    result["winner_id"] = int(result["winner_id"]) if result.get("winner_id") is not None else None
    result["version"] = int(result.get("version") or 0)
    result["is_active"] = result.get("status") == "active"
    return result


async def create_session(
    *,
    guild_id: int,
    channel_id: int,
    game_key: str,
    players: list[int],
) -> dict[str, Any]:
    """Create a durable session; attach_message binds it to its Discord message."""
    game = registry.get(game_key)
    clean_players = [int(value) for value in players]
    if len(clean_players) != len(set(clean_players)):
        raise ValueError("player IDs must be unique")
    if not game.min_players <= len(clean_players) <= game.max_players:
        raise ValueError("invalid_player_count")
    if any(value <= 0 for value in clean_players):
        raise ValueError("invalid_player_id")

    state = game.initial_state(clean_players)
    async with connect(aiosqlite.Row) as db:
        cur = await db.execute(
            """
            INSERT INTO prime_game_sessions
                (guild_id, channel_id, game_key, players_json, state_json,
                 current_player_id, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                int(guild_id), int(channel_id), game.key,
                json.dumps(clean_players), json.dumps(state), clean_players[0],
            ),
        )
        session_id = int(cur.lastrowid)
        await db.commit()
        async with db.execute(
            "SELECT * FROM prime_game_sessions WHERE id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
    value = _session(row)
    if value is None:
        raise RuntimeError("new game session could not be reloaded")
    return value


async def attach_message(session_id: int, message_id: int) -> bool:
    """Attach an active session to its published Discord message exactly once."""
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE prime_game_sessions
            SET message_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'active' AND message_id = 0
            """,
            (int(message_id), int(session_id)),
        )
        await db.commit()
    return cur.rowcount == 1


async def cancel_session(session_id: int, *, guild_id: int | None = None) -> bool:
    query = """
        UPDATE prime_game_sessions
        SET status = 'cancelled', result = 'cancelled',
            updated_at = CURRENT_TIMESTAMP, version = version + 1
        WHERE id = ? AND status = 'active'
    """
    params: list[int] = [int(session_id)]
    if guild_id is not None:
        query += " AND guild_id = ?"
        params.append(int(guild_id))
    async with connect() as db:
        cur = await db.execute(query, tuple(params))
        await db.commit()
    return cur.rowcount == 1


async def get_session(session_id: int) -> dict[str, Any] | None:
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM prime_game_sessions WHERE id = ?", (int(session_id),)
        ) as cur:
            row = await cur.fetchone()
    return _session(row)


async def list_active_sessions(guild_id: int | None = None) -> list[dict[str, Any]]:
    """List published live sessions, optionally scoped to one Discord server."""
    query = """
        SELECT * FROM prime_game_sessions
        WHERE status = 'active' AND message_id > 0
    """
    params: tuple[int, ...] = ()
    if guild_id is not None:
        query += " AND guild_id = ?"
        params = (int(guild_id),)
    query += " ORDER BY created_at DESC, id DESC LIMIT 100"
    async with connect(aiosqlite.Row) as db:
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    return [value for value in (_session(row) for row in rows) if value is not None]


def _failure(reason: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason}


async def apply_action(
    session_id: int,
    actor_id: int,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Apply an action with turn checks, versioning and rewards in one transaction."""
    async with connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT * FROM prime_game_sessions WHERE id = ?", (int(session_id),)
        ) as cur:
            row = await cur.fetchone()
        session = _session(row)
        if session is None:
            await db.rollback()
            return _failure("session_not_found")
        if session["status"] != "active":
            await db.rollback()
            return _failure("game_finished")
        players = session["players"]
        if int(actor_id) not in players:
            await db.rollback()
            return _failure("not_a_player")
        if session["current_player_id"] != int(actor_id):
            await db.rollback()
            return _failure("not_your_turn")

        try:
            game = registry.get(session["game_key"])
            transition: GameTransition = game.reduce_action(
                session["state"], players, int(actor_id), action
            )
        except ValueError as error:
            await db.rollback()
            return _failure(str(error))

        finished = transition.winner_id is not None or transition.is_draw
        status = "finished" if finished else "active"
        result_text = (
            f"winner:{int(transition.winner_id)}"
            if transition.winner_id is not None
            else ("draw" if transition.is_draw else "")
        )
        cur = await db.execute(
            """
            UPDATE prime_game_sessions
            SET state_json = ?, current_player_id = ?, status = ?,
                winner_id = ?, result = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ? AND status = 'active'
            """,
            (
                json.dumps(transition.state), transition.next_player_id, status,
                transition.winner_id, result_text, int(session_id), session["version"],
            ),
        )
        if cur.rowcount != 1:
            await db.rollback()
            return _failure("stale_game_state")

        reward = 0
        if transition.winner_id is not None and game.reward_coins:
            reward_cur = await db.execute(
                """
                INSERT OR IGNORE INTO prime_game_reward_claims
                    (session_id, guild_id, user_id, amount)
                VALUES (?, ?, ?, ?)
                """,
                (
                    int(session_id), session["guild_id"],
                    int(transition.winner_id), int(game.reward_coins),
                ),
            )
            if reward_cur.rowcount == 1:
                # Use PRIME's existing wallet and ledger, not a second economy.
                await db.execute(
                    "INSERT OR IGNORE INTO users (user_id, guild_id) VALUES (?, ?)",
                    (int(transition.winner_id), session["guild_id"]),
                )
                await db.execute(
                    "UPDATE users SET balance = balance + ? WHERE user_id = ? AND guild_id = ?",
                    (int(game.reward_coins), int(transition.winner_id), session["guild_id"]),
                )
                await db.execute(
                    """
                    INSERT INTO economy_transactions
                        (guild_id, from_user_id, to_user_id, amount, kind)
                    VALUES (?, NULL, ?, ?, 'game_reward')
                    """,
                    (session["guild_id"], int(transition.winner_id), int(game.reward_coins)),
                )
                reward = int(game.reward_coins)

        await db.commit()
    updated = await get_session(int(session_id))
    return {
        "ok": True,
        "session": updated,
        "reward": reward,
        "finished": finished,
        "winner_id": transition.winner_id,
        "is_draw": transition.is_draw,
    }
