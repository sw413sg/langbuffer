"""Reglas iniciales de lectura; no equivalen a tiempos acústicos."""
import textwrap


def caption_pages(text, width=42):
    """Hasta dos líneas por página, sin eliminar palabras."""
    normalized = ' '.join(text.split())
    lines = textwrap.wrap(normalized, width=width, break_long_words=True,
                          break_on_hyphens=False)
    groups = [lines[i:i+2] for i in range(0, len(lines), 2)]
    if len(groups) > 1 and len(' '.join(groups[-1]).split()) <= 2 and len(groups[-2]) == 2:
        # Evitar una palabra residual aislada: conservarla junto a la línea previa.
        groups[-1].insert(0, groups[-2].pop())
    return ['\n'.join(group) for group in groups]


def bounded_phrases(text, max_chars=140, max_words=24):
    """Acota unidades de origen; prefiere pausas escritas cuando hay que cortar."""
    words = text.split()
    while words:
        count = 1
        while count < min(max_words, len(words)) and len(' '.join(words[:count+1])) <= max_chars:
            count += 1
        if count < len(words):
            pauses = [i+1 for i in range(count) if words[i].endswith((',', ';', ':')) and i >= 5]
            if pauses:
                count = pauses[-1]
        yield ' '.join(words[:count])
        words = words[count:]
