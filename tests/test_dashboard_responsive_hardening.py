"""Regression checks for responsive dashboard styles and cache invalidation."""

import re
import unittest
from pathlib import Path

import web_server


ROOT = Path(__file__).resolve().parents[1]


class DashboardResponsiveHardeningTests(unittest.TestCase):
    def test_mobile_overrides_cover_narrow_layout_and_touch_tables(self):
        styles = (ROOT / "dashboard/visual-refresh.css").read_text("utf-8")
        self.assertIn("@media (max-width: 640px)", styles)
        self.assertIn("@media (max-width: 380px)", styles)
        self.assertIn("overscroll-behavior-inline: contain", styles)
        self.assertIn("-webkit-overflow-scrolling: touch", styles)
        self.assertIn("env(safe-area-inset-bottom)", styles)

    def test_reduced_motion_preference_is_respected(self):
        styles = (ROOT / "dashboard/visual-refresh.css").read_text("utf-8")
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)
        self.assertIn("animation-duration: .01ms !important", styles)
        self.assertIn("scroll-behavior: auto !important", styles)

    def test_html_and_service_worker_use_same_versioned_stylesheet(self):
        html = (ROOT / "dashboard/index.html").read_text("utf-8")
        worker = web_server.service_worker_source()
        match = re.search(
            r'href="static/visual-refresh\.css\?v=([^"]+)"',
            html,
        )
        self.assertIsNotNone(match)
        asset = f"./static/visual-refresh.css?v={match.group(1)}"
        self.assertIn(f'"{asset}"', worker)
        self.assertEqual(match.group(1), "mobile-hardening-1")
        self.assertIn('const CACHE = "prime-dashboard-shell-v47";', worker)

    def test_guild_recovery_script_is_cache_busted_in_html_and_worker(self):
        html = (ROOT / "dashboard/index.html").read_text("utf-8")
        worker = web_server.service_worker_source()
        match = re.search(
            r'<script src="static/app\.js\?v=([^"]+)"',
            html,
        )
        self.assertIsNotNone(match)
        version = match.group(1)
        self.assertEqual(version, "bot-guild-discovery-1")
        self.assertIn(f'"./static/app.js?v={version}"', worker)
