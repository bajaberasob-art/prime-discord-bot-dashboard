"""Regression checks for Roulette Royale's turn-resolution guards."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_game_modules_compile():
    files = [
        "game_engine.py",
        "cogs/roulette_royale.py",
        "prime_games/engine.py",
    ]
    subprocess.run(
        [sys.executable, "-m", "py_compile", *files],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_turn_view_claims_decision_before_timeout_resolution():
    source = (ROOT / "cogs/roulette_royale.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    view = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RoyaleTurnView")
    methods = {node.name: node for node in view.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_claim_decision" in methods
    timeout_source = ast.get_source_segment(source, methods["on_timeout"])
    assert "_claim_decision()" in timeout_source
    assert "if not self._claim_decision()" in timeout_source


def test_pass_reroll_is_serialized_not_spawned():
    source = (ROOT / "cogs/roulette_royale.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    game = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RouletteRoyaleGame")
    resolve = next(node for node in game.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "resolve_turn")
    run = next(node for node in game.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "run")
    resolve_source = ast.get_source_segment(source, resolve)
    run_source = ast.get_source_segment(source, run)
    assert "self._pending_pass_target =" in resolve_source
    assert "asyncio.create_task(self._reroll_target" not in resolve_source
    assert "while self._pending_pass_target is not None" in run_source


def test_skill_consumption_persists_on_player_state():
    source = (ROOT / "cogs/roulette_royale.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    player = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RoyalePlayer")
    assert any(isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "used_skills" for node in player.body)
    assert 'button.disabled = skill in self.game.players[self.target_id].used_skills' in source
