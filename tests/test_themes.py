"""Custom theme math and building (no window needed)."""
import unittest

import themes


class ThemeMathTests(unittest.TestCase):
    def test_mix(self):
        self.assertEqual(themes.mix('#000000', '#ffffff'), '#808080')
        self.assertEqual(themes.mix('#102030', '#102030', 0.7), '#102030')

    def test_clean_hex(self):
        self.assertEqual(themes.clean_hex('ABC'), '#aabbcc')
        self.assertEqual(themes.clean_hex('#12ab34'), '#12ab34')
        self.assertIsNone(themes.clean_hex('#12'))
        self.assertIsNone(themes.clean_hex('blue'))

    def test_contrast(self):
        self.assertAlmostEqual(themes.contrast('#000000', '#ffffff'), 21, places=1)
        self.assertAlmostEqual(themes.contrast('#777777', '#777777'), 1)

    def test_build_custom_has_every_key_and_detects_dark(self):
        dark = themes.build_custom('modern', {'bg': '#0b1d2a', 'surface': '#12293a', 'ink': '#e8f4ff', 'accent': '#00b4d8'})
        self.assertEqual(set(themes.THEMES['modern']) - set(dark), set())
        self.assertTrue(dark['is_dark'])
        light = themes.build_custom('retro', {'accent': '#aa0055'})
        self.assertFalse(light['is_dark'])
        self.assertTrue(light['bevel'])

    def test_header_bar_off_and_bad_colors_ignored(self):
        t = themes.build_custom('retro', {'header_band': None, 'ink': 'not a color'})
        self.assertIsNone(t['header_band'])
        self.assertEqual(t['ink'], themes.THEMES['retro']['ink'])

    def test_set_custom_themes_skips_damaged_entries(self):
        themes.set_custom_themes({'a': {'name': 'Ocean', 'base': 'modern', 'colors': {'accent': '#0077b6'}},
                                  'b': {'name': 'Broken', 'base': 'modern', 'colors': 'oops'}})
        try:
            self.assertIn('a', themes.all_themes())
            self.assertEqual(themes.all_themes()['a']['name'], 'Ocean')
            self.assertNotIn('b', themes.CUSTOM_THEMES)
        finally:
            themes.set_custom_themes({})


if __name__ == '__main__':
    unittest.main()
