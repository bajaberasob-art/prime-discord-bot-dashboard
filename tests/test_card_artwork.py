import unittest
from unittest.mock import patch

from PIL import Image

import cogs.card_artwork as card_artwork
from cogs.card_artwork import _ART, _LAYOUT_ART, render_card_artwork


class CardArtworkLibraryTests(unittest.TestCase):
    def setUp(self):
        card_artwork._rasterize_artwork.cache_clear()

    def tearDown(self):
        card_artwork._rasterize_artwork.cache_clear()

    def test_every_layout_maps_to_an_existing_local_svg(self):
        self.assertEqual(
            set(_LAYOUT_ART),
            {"vertical", "stats", "minimal", "ring", "classic", "banner",
             "square", "spotlight", "showcase"},
        )
        self.assertEqual(len(set(_LAYOUT_ART.values())), 9)
        for filename in set(_LAYOUT_ART.values()):
            self.assertTrue((_ART / filename).is_file(), filename)

    def test_svg_artwork_rasterizes_to_requested_size(self):
        for layout in _LAYOUT_ART:
            with self.subTest(layout=layout):
                image = render_card_artwork(layout, (320, 180))
                self.assertIsInstance(image, Image.Image)
                self.assertEqual(image.size, (320, 180))
                self.assertGreater(len(set(image.getdata())), 100)

    def test_repeated_render_uses_cached_png_but_returns_independent_images(self):
        with patch.object(card_artwork.cairosvg, "svg2png", wraps=card_artwork.cairosvg.svg2png) as rasterize:
            first = render_card_artwork("showcase", (160, 90))
            second = render_card_artwork("showcase", (160, 90))

        self.assertEqual(rasterize.call_count, 1)
        self.assertIsNot(first, second)
        self.assertEqual(first.size, second.size)
        first.putpixel((0, 0), (255, 0, 0))
        self.assertNotEqual(first.getpixel((0, 0)), second.getpixel((0, 0)))

    def test_cache_keeps_different_sizes_separate(self):
        small = render_card_artwork("showcase", (160, 90))
        large = render_card_artwork("showcase", (320, 180))
        self.assertEqual(small.size, (160, 90))
        self.assertEqual(large.size, (320, 180))
        self.assertEqual(card_artwork._rasterize_artwork.cache_info().misses, 2)

    def test_unknown_layout_returns_none(self):
        self.assertIsNone(render_card_artwork("not-a-layout", (100, 100)))


if __name__ == "__main__":
    unittest.main()
