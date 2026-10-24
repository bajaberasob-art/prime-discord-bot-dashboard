import unittest

from PIL import Image

from cogs.card_artwork import _ART, _LAYOUT_ART, render_card_artwork


class CardArtworkLibraryTests(unittest.TestCase):
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

    def test_unknown_layout_returns_none(self):
        self.assertIsNone(render_card_artwork("not-a-layout", (100, 100)))


if __name__ == "__main__":
    unittest.main()
