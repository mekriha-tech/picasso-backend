from app.services.badges import compute_badge


def test_auction_is_live_auction():
    assert compute_badge("auction", "published", False) == "Live Auction"


def test_sale_is_for_sale():
    assert compute_badge("sale", "published", False) == "For Sale"


def test_display_sold_is_sold():
    assert compute_badge("display", "sold", True) == "Sold"


def test_display_not_sold_is_on_display():
    assert compute_badge("display", "published", False) == "On Display"
