"""Regression tests for preserving motion in uploaded level-card GIFs."""

import io
import unittest

from PIL import Image

from cogs.card_images import MAX_ANIMATED_FRAMES, normalize_image


def make_gif(frame_count, size=(96, 72)):
    frames = []
    for index in range(frame_count):
        frame = Image.new(
            "RGB",
            size,
            (index * 7 % 255, index * 13 % 255, index * 19 % 255),
        )
        frames.append(frame)
    output = io.BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=[40 + (index % 3) * 10 for index in range(frame_count)],
        loop=0,
        disposal=2,
    )
    return output.getvalue()


class AnimatedGifNormalizationTests(unittest.TestCase):
    def test_gif_over_frame_budget_stays_animated_and_is_sampled(self):
        raw = make_gif(MAX_ANIMATED_FRAMES + 8)
        normalized = normalize_image(raw)

        self.assertTrue(normalized.startswith((b"GIF87a", b"GIF89a")))
        with Image.open(io.BytesIO(normalized)) as image:
            self.assertGreater(image.n_frames, 1)
            self.assertLessEqual(image.n_frames, MAX_ANIMATED_FRAMES)

    def test_small_animated_gif_remains_a_gif(self):
        raw = make_gif(5)
        normalized = normalize_image(raw)

        self.assertTrue(normalized.startswith((b"GIF87a", b"GIF89a")))
        with Image.open(io.BytesIO(normalized)) as image:
            self.assertEqual(image.n_frames, 5)


if __name__ == "__main__":
    unittest.main()
