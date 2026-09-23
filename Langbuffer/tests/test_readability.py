import unittest
from langbuffer.readability import caption_pages, bounded_phrases

PARAGRAPH = ('La aplicación prepara la traducción mientras seguimos escuchando la conversación. '
             'Cada subtítulo debe ser breve y permanecer el tiempo suficiente para leerlo. '
             'El texto pendiente debe aparecer después, sin eliminar la parte final del mensaje.')


class ReadabilityTests(unittest.TestCase):
    def test_two_lines_without_lost_words(self):
        pages = caption_pages(PARAGRAPH)
        self.assertGreater(len(pages), 1)
        self.assertEqual(' '.join(' '.join(p.split()) for p in pages), PARAGRAPH)
        for page in pages:
            self.assertLessEqual(len(page.splitlines()), 2)
            self.assertTrue(all(len(line) <= 42 for line in page.splitlines()))





    def test_prefers_clause_boundary(self):
        text = 'These are the first words of a clause, followed by a longer explanation with many extra words to finish.'
        units = list(bounded_phrases(text, max_chars=80))
        self.assertTrue(units[0].endswith(','))
        self.assertEqual(' '.join(units), text)





