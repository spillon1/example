from instamap.places import clean_name, find_places, pattern_candidates, plausible

CAPTION = """Lisbon in 48 hours 🇵🇹 save this for your trip!

📍 Pastéis de Belém - the original custard tarts
📍 Miradouro da Senhora do Monte, Lisbon
Location: Time Out Market

My top 3 dinners:
1. Cervejaria Ramiro – seafood
2. Taberna da Rua das Flores
3) A Cevicheria

Follow @wanderer for more #lisbon #travel
"""


def names(cands):
    return {c.name for c in cands}


def test_pin_lines_and_numbered_lists():
    got = names(pattern_candidates(CAPTION, "caption"))
    assert {"Pastéis de Belém", "Miradouro da Senhora do Monte", "Time Out Market",
            "Cervejaria Ramiro", "Taberna da Rua das Flores", "A Cevicheria"} <= got


def test_area_split_from_comma():
    c = next(c for c in pattern_candidates(CAPTION, "caption") if c.name.startswith("Miradouro"))
    assert c.area == "Lisbon"


def test_noise_filtered():
    assert not plausible("Follow")
    assert not plausible("12")
    assert not plausible("$45")
    assert clean_name("  ☕️ Café Janis!! ") == "Café Janis"


def test_find_places_without_llm_or_spacy(settings):
    found = find_places(caption=CAPTION, transcript="", ocr="📍 LX Factory", username="wanderer",
                        ig_tag={"name": "Lisbon, Portugal", "city": "Lisbon"},
                        ollama_url=settings.ollama_url, ollama_model="x")
    by_name = {c.name: c for c in found}
    assert "LX Factory" in by_name and by_name["LX Factory"].method == "ocr"
    assert by_name["Pastéis de Belém"].area == "Lisbon"


def test_repeated_mentions_boost_confidence(settings):
    found = find_places(caption="📍 LX Factory", transcript="", ocr="📍 LX Factory", username=None,
                        ig_tag=None, ollama_url=settings.ollama_url, ollama_model="x")
    assert len(found) == 1 and found[0].confidence > 0.85


def test_bare_overlay_title_is_a_weak_candidate(settings):
    found = find_places(caption="", transcript="", ocr="Time Out Market\nbest food hall in town", username=None,
                        ig_tag=None, ollama_url=settings.ollama_url, ollama_model="x")
    tom = next(c for c in found if c.name == "Time Out Market")
    assert tom.confidence < 0.5
    assert all(c.name != "best food hall in town" for c in found)
