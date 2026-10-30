"""Regression coverage for welcome image variables and Unicode typography."""

from io import BytesIO
import unittest

from PIL import Image

from cogs.engagement import Engagement


class WelcomeImageVariableTests(unittest.TestCase):
    def test_all_dashboard_tokens_resolve_in_image_text(self):
        rendered = Engagement._welcome_image_template(
            "أهلاً {user} / {username} — {server} — #{count} — {inviter} — {invite_code}",
            "عبودي",
            "PRIME TEAM",
            283,
            inviter="صالح",
            invite_code="AB12CD",
        )
        self.assertEqual(
            rendered,
            "أهلاً عبودي / عبودي — PRIME TEAM — #283 — صالح — AB12CD",
        )

    def test_image_template_data_can_override_values_and_unknown_tokens_are_removed(self):
        rendered = Engagement._welcome_image_template(
            "{user}|{count}|{inviter}|{invite_code}|{unknown_token}",
            "Member",
            "Server",
            12,
            inviter="Inviter",
            invite_code="CODE",
            template_data={"user": "Preview User", "count": 99},
        )
        self.assertEqual(rendered, "Preview User|99|Inviter|CODE|")

    def test_dynamic_variables_are_rasterized_in_the_real_png_path(self):
        avatar = Image.new("RGB", (48, 48), (80, 110, 180))
        stream = BytesIO()
        avatar.save(stream, format="PNG")
        config = {
            "canvas_format": "banner",
            "elements": [{
                "id": "welcome-dynamic-text",
                "type": "text",
                "x": 3,
                "y": 15,
                "width": 94,
                "height": 70,
                "fontSize": 34,
                "color": "#ffffff",
                "visible": True,
                "opacity": 100,
                "rotation": 0,
                "align": "right",
                "text": "{user} · {server} · {count} · {inviter} · {invite_code}",
                "fontFamily": "sans",
                "fontWeight": "bold",
                "fontStyle": "normal",
            }],
        }
        output = Engagement._render_welcome_image(
            stream.getvalue(),
            "عبودي",
            "PRIME TEAM",
            283,
            "#7c3aed",
            config,
            inviter="صالح",
            invite_code="AB12CD",
        )
        rendered = Image.open(output).convert("RGB")
        crop = rendered.crop((35, 40, 1165, 360))
        self.assertGreater(len(crop.getcolors(crop.width * crop.height) or []), 2)

    def test_fontstack_preprocessor_preserves_arabic_harakat(self):
        import fontstack.manager as fontstack_manager

        manager = Engagement._welcome_font_manager("sans", "normal")
        reshaped = fontstack_manager._prepare_bidi("السَّلَامُ عَلَيْكُمْ")
        self.assertTrue(any(char in reshaped for char in "\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652"))

        marked = Image.new("RGBA", (1000, 180), (8, 12, 20, 255))
        plain = Image.new("RGBA", (1000, 180), (8, 12, 20, 255))
        sample_marked = "السَّلَامُ عَلَيْكُمْ"
        sample_plain = "السلام عليكم"
        manager.draw(marked, sample_marked, position=(20, 20), size=54, fill=(255, 255, 255, 255))
        manager.draw(plain, sample_plain, position=(20, 20), size=54, fill=(255, 255, 255, 255))
        self.assertNotEqual(marked.tobytes(), plain.tobytes())

    def test_unicode_fallback_renders_arabic_marks_and_multiple_scripts(self):
        image = Image.new("RGBA", (1160, 260), (8, 12, 20, 255))
        manager = Engagement._welcome_font_manager("sans", "normal")
        sample = "السَّلَامُ عَلَيْكُمْ َِ ُّ | PRIME 2026 | हिन्दी | 日本語 | Ω ★ ∑"
        width, height = manager.draw(
            image,
            sample,
            position=(24, 24),
            size=38,
            weight=400,
            mode="fit",
            max_width=1110,
            max_height=210,
            min_size=12,
            align="left",
            fill=(245, 248, 255, 255),
        )
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)
        self.assertGreater(len(image.getcolors(image.width * image.height) or []), 2)


if __name__ == "__main__":
    unittest.main()
