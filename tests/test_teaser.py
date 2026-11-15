from app.services.teaser import (
    LOGIN_REQUIRED_MESSAGE,
    TEASER_LIMIT,
    anonymous_artwork_list_violation,
    teaser_limit,
)


def _violation(**overrides):
    params = dict(category=None, artist_id=None, min_price=None, max_price=None, q=None, sort="newest", cursor=None)
    params.update(overrides)
    return anonymous_artwork_list_violation(**params)


def test_plain_request_is_allowed():
    assert _violation() is None


def test_empty_search_string_is_allowed():
    assert _violation(q="") is None


def test_cursor_requires_login():
    assert _violation(cursor="abc") == LOGIN_REQUIRED_MESSAGE


def test_each_filter_requires_login():
    for override in (
        {"category": "painting"},
        {"artist_id": "11111111-1111-1111-1111-111111111111"},
        {"min_price": "1"},
        {"max_price": "9"},
        {"q": "sunset"},
    ):
        assert _violation(**override) == LOGIN_REQUIRED_MESSAGE, override


def test_non_default_sort_requires_login():
    for sort in ("price_asc", "price_desc", "ending_soon"):
        assert _violation(sort=sort) == LOGIN_REQUIRED_MESSAGE, sort


def test_limit_is_capped_for_anonymous():
    assert teaser_limit(100) == TEASER_LIMIT
    assert teaser_limit(5) == 5
