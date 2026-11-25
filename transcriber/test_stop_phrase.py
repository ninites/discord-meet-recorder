import unittest

from stop_phrase import ends_with_stop_phrase, matching_stop_phrase, parse_stop_phrases

PHRASE = "merci Claude pour les travaux"


class StopPhraseTest(unittest.TestCase):
    def test_reconnait_la_phrase_et_ses_variantes(self):
        for text in [
            "Merci Claude pour les travaux.",
            "Bon, on a fait le tour. Merci, Claude, pour les travaux !",
            "merci clode pour les travaux",
            "Merci Claude pour les travaux, à plus tout le monde",
            "Merci Claude pour lestravaux",
        ]:
            with self.subTest(text=text):
                self.assertTrue(ends_with_stop_phrase(text, PHRASE))

    def test_ignore_les_phrases_proches_ou_au_milieu(self):
        for text in [
            "Merci pour les travaux sur la base de données.",
            "Il suffit de dire merci Claude pour les travaux et ça coupe le meet, c'est pratique non ?",
            "Merci Claude.",
            "On reprend les travaux lundi.",
            "",
        ]:
            with self.subTest(text=text):
                self.assertFalse(ends_with_stop_phrase(text, PHRASE))

    def test_phrase_vide_desactive(self):
        self.assertFalse(ends_with_stop_phrase("Merci Claude pour les travaux", ""))



class PlusieursPhrasesTest(unittest.TestCase):
    PHRASES = parse_stop_phrases(" merci Claude pour les travaux | Claude, coupe l'enregistrement ||")

    def test_lecture_de_la_variable(self):
        self.assertEqual(self.PHRASES, ["merci Claude pour les travaux", "Claude, coupe l'enregistrement"])
        self.assertEqual(parse_stop_phrases("merci Claude pour les travaux"), ["merci Claude pour les travaux"])
        self.assertEqual(parse_stop_phrases(""), [])
        self.assertEqual(parse_stop_phrases(None), [])

    def test_chaque_phrase_arrete(self):
        self.assertEqual(matching_stop_phrase("Bon, merci Claude pour les travaux !", self.PHRASES), self.PHRASES[0])
        self.assertEqual(matching_stop_phrase("Allez, Claude, coupe l'enregistrement.", self.PHRASES), self.PHRASES[1])

    def test_aucune_phrase(self):
        self.assertIsNone(matching_stop_phrase("On coupe là pour aujourd'hui.", self.PHRASES))
        self.assertIsNone(matching_stop_phrase("Claude coupe l'enregistrement quand on lui dit, c'est pratique non ?", self.PHRASES))
        self.assertIsNone(matching_stop_phrase("Merci Claude pour les travaux", []))


if __name__ == "__main__":
    unittest.main()
