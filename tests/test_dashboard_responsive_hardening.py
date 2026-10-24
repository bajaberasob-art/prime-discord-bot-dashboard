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
        self.assertEqual(match.group(1), "prime-card-showcase-layout-1")
        self.assertIn('const CACHE = "prime-dashboard-shell-v55";', worker)

    def test_level_card_preview_tools_and_cache_version_are_wired(self):
        html = (ROOT / "dashboard/index.html").read_text("utf-8")
        worker = web_server.service_worker_source()
        app = (ROOT / "dashboard/app.js").read_text("utf-8")
        styles = (ROOT / "dashboard/visual-refresh.css").read_text("utf-8")
        match = re.search(
            r'<script src="static/app\.js\?v=([^"]+)"',
            html,
        )
        self.assertIsNotNone(match)
        version = match.group(1)
        self.assertEqual(version, "prime-card-showcase-layout-1")
        self.assertIn(f'"./static/app.js?v={version}"', worker)
        self.assertIn('const CACHE = "prime-dashboard-shell-v55";', worker)
        self.assertIn('text: "تحديث المعاينة"', app)
        self.assertIn('text: "تنزيل البطاقة"', app)
        self.assertIn("previewBlob.type", app)
        self.assertIn(".lv-preview-actions", styles)
        self.assertIn("@media (max-width: 480px)", styles)

    def test_portable_card_presets_are_validated_and_cache_busted(self):
        html = (ROOT / "dashboard/index.html").read_text("utf-8")
        worker = web_server.service_worker_source()
        app = (ROOT / "dashboard/app.js").read_text("utf-8")
        styles = (ROOT / "dashboard/visual-refresh.css").read_text("utf-8")
        self.assertIn('const CACHE = "prime-dashboard-shell-v55";', worker)
        self.assertIn('const LV_CARD_PRESET_FORMAT = "prime-level-card-presets";', app)
        self.assertIn("LV_CARD_PRESET_VERSION = 1", app)
        self.assertIn('text: "تصدير الإعداد الحالي"', app)
        self.assertIn('text: "تصدير جميع الإعدادات"', app)
        self.assertIn('text: "استيراد إعداد JSON"', app)
        self.assertIn("file.size > 64 * 1024", app)
        self.assertIn("existing.length + parsed.presets.length > 8", app)
        self.assertIn("lvImportedPresetStyle(item.style)", app)
        self.assertIn('style.backgroundAsset = "";', app)
        self.assertIn("./static/app.js?v=prime-card-showcase-layout-1", worker)
        self.assertIn('src="static/app.js?v=prime-card-showcase-layout-1"', html)
        self.assertIn(".lv-preset-portability", styles)
        self.assertIn("@media (max-width: 480px)", styles)

    def test_ninth_showcase_layout_is_wired_to_renderer_api_and_gallery(self):
        import leveling_api
        from cogs import card_generator

        app = (ROOT / "dashboard/app.js").read_text("utf-8")
        self.assertIn("showcase", card_generator.LAYOUTS)
        self.assertEqual(card_generator.LAYOUTS["showcase"], (1100, 460))
        self.assertIn("showcase", leveling_api.LAYOUTS)
        self.assertIn('showcase: [1100, 460, "استعراضية"]', app)
        self.assertIn('id: "showcase"', app)
        self.assertIn("تسعة تخطيطات عملية", app)

    def test_card_studio_themes_history_and_upload_copy_are_wired(self):
        app = (ROOT / "dashboard/app.js").read_text("utf-8")
        styles = (ROOT / "dashboard/visual-refresh.css").read_text("utf-8")
        self.assertIn("const LV_CARD_THEMES = [", app)
        self.assertIn('id: "cyberpunk"', app)
        self.assertIn('id: "luxury"', app)
        self.assertIn('id: "cosmic"', app)
        self.assertIn('id: "mythic"', app)
        self.assertIn('id: "showcase"', app)
        self.assertIn('visualStyle: "cyberpunk"', app)
        self.assertIn('lvSelect(["card", "visualStyle"]', app)
        self.assertIn("function lvCardHistoryMove(delta)", app)
        self.assertIn("LV_CARD_HISTORY_LIMIT = 40", app)
        self.assertIn("lvCardRestoreSaved", app)
        self.assertIn("استرجاع المحفوظ", app)
        self.assertIn("GIF حتى 15 MiB", app)
        self.assertNotIn("GIF حتى 6MB", app)
        self.assertIn(".lv-theme-gallery", styles)
        self.assertIn(".lv-theme-swatch-showcase", styles)
        self.assertIn(".lv-card-history-actions", styles)
        self.assertIn("@media (max-width: 380px)", styles)
        self.assertIn("prefers-reduced-motion: reduce", styles)

    def test_level_evolution_setting_is_saved_previewed_and_cache_busted(self):
        html = (ROOT / "dashboard/index.html").read_text("utf-8")
        worker = web_server.service_worker_source()
        app = (ROOT / "dashboard/app.js").read_text("utf-8")
        styles = (ROOT / "dashboard/visual-refresh.css").read_text("utf-8")
        self.assertIn('"evolutionEnabled"', app)
        self.assertIn('lvSwitch(["card", "evolutionEnabled"], "تطور البطاقة حسب المستوى")', app)
        self.assertIn('evolutionEnabled: String(card.evolutionEnabled !== false)', app)
        self.assertIn("lv-evolution-track", app)
        self.assertIn("100+", app)
        self.assertIn(".lv-evolution-track", styles)
        self.assertIn("@media (max-width: 480px)", styles)
        self.assertIn("prime-card-showcase-layout-1", html)
        self.assertIn("./static/app.js?v=prime-card-showcase-layout-1", worker)
        self.assertIn("./static/visual-refresh.css?v=prime-card-showcase-layout-1", worker)
        self.assertIn('const CACHE = "prime-dashboard-shell-v55";', worker)

    def test_guild_recovery_script_is_cache_busted_in_html_and_worker(self):
        html = (ROOT / "dashboard/index.html").read_text("utf-8")
        worker = web_server.service_worker_source()
        match = re.search(
            r'<script src="static/app\.js\?v=([^"]+)"',
            html,
        )
        self.assertIsNotNone(match)
        version = match.group(1)
        self.assertEqual(version, "prime-card-showcase-layout-1")
        self.assertIn(f'"./static/app.js?v={version}"', worker)
