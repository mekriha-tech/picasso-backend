"""What an anonymous (logged-out) visitor may see of the public catalogue.

Product rule: logged-out visitors get a teaser, not the full gallery. It is enforced here on
the server - a UI-only limit could be bypassed by calling the public API directly and
downloading every work at full quality.
"""

TEASER_LIMIT = 12
LOGIN_REQUIRED_MESSAGE = "Log in to see more of the gallery."


def anonymous_artwork_list_violation(
    *, category, artist_id, min_price, max_price, q, sort, cursor
) -> str | None:
    """Returns the 401 message if an anonymous caller asked for more than the teaser.

    Allowed for anonymous callers: the default listing (newest first), optionally narrowed
    by listing_type. Everything that could be used to walk the whole catalogue - paging,
    searching, other sorts, price/category/artist filters - needs a login.
    """
    if (
        cursor is not None
        or category is not None
        or artist_id is not None
        or min_price is not None
        or max_price is not None
        or q
        or sort != "newest"
    ):
        return LOGIN_REQUIRED_MESSAGE
    return None


def teaser_limit(requested: int) -> int:
    return min(requested, TEASER_LIMIT)
