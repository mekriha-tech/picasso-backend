def compute_badge(listing_type: str, status: str, sold: bool) -> str:
    """PRD §5's badge mapping. listing_type takes priority over sold - by PRD rule 20, a sold
    artwork always flips to listing_type='display' anyway, so a "sale"+sold combination
    shouldn't occur in practice, but this function is deterministic either way."""
    if listing_type == "auction":
        return "Live Auction"
    if listing_type == "sale":
        return "For Sale"
    if sold:
        return "Sold"
    return "On Display"
