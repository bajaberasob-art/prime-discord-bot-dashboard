import unittest

from PIL import Image

from cogs.games import (
    GameCatalogView,
    _GAME_CATEGORIES,
    _game_catalog_banner,
    _game_catalog_embed,
)


class GameCatalogPresentationTests(unittest.TestCase):
    def test_catalog_banner_is_a_valid_wide_png(self):
        banner = _game_catalog_banner()
        try:
            image = Image.open(banner.fp)
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (1100, 300))
            self.assertEqual(banner.filename, "prime-game-list.png")
        finally:
            banner.close()

    def test_category_embeds_only_include_games_from_selected_category(self):
        all_embed = _game_catalog_embed("all")
        mind_embed = _game_catalog_embed("mind")
        strategy_embed = _game_catalog_embed("strategy")
        self.assertEqual(len(all_embed.fields), 7)
        self.assertEqual(
            {field.name.split("  •")[0] for field in mind_embed.fields},
            {"تحدّي الذاكرة", "فكّ الكلمة", "تحدّي المعلومات"},
        )
        self.assertEqual(
            {field.name.split("  •")[0] for field in strategy_embed.fields},
            {"XO", "أربعة على التوالي", "حجر ورقة مقص", "مبارزة النرد"},
        )
        self.assertTrue(all_embed.image.url.endswith("prime-game-list.png"))

    def test_category_select_has_valid_values_and_is_attached_to_view(self):
        view = GameCatalogView()
        select = view.children[0]
        self.assertEqual({option.value for option in select.options}, set(_GAME_CATEGORIES))
        self.assertEqual(select.min_values, 1)
        self.assertEqual(select.max_values, 1)


if __name__ == "__main__":
    unittest.main()
