from app.services.slugs import slugify, generate_unique_slug


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Elena D' Frost") == "elena-d-frost"


def test_slugify_strips_leading_trailing_punctuation():
    assert slugify("  --Abstract Painting!!--  ") == "abstract-painting"


def test_slugify_empty_input_falls_back():
    assert slugify("###") == "item"


def test_generate_unique_slug_no_collision():
    assert generate_unique_slug("Unique Name", set()) == "unique-name"


def test_generate_unique_slug_single_collision():
    assert generate_unique_slug("Elena Frost", {"elena-frost"}) == "elena-frost-2"


def test_generate_unique_slug_chained_collision():
    existing = {"elena-frost", "elena-frost-2", "elena-frost-3"}
    assert generate_unique_slug("Elena Frost", existing) == "elena-frost-4"
