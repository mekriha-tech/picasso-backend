import re


def slugify(text: str) -> str:
    """Lowercases, strips to alphanumerics-and-hyphens. Never returns an empty string."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


def generate_unique_slug(base_text: str, existing_slugs: set[str]) -> str:
    """Slugifies base_text, appending -2, -3, ... until it doesn't collide with existing_slugs."""
    base = slugify(base_text)
    if base not in existing_slugs:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing_slugs:
        suffix += 1
    return f"{base}-{suffix}"
