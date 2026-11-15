"""Content for scripts/seed_dev.py: artists, applicants and artworks (no secrets here).

Artwork images are public-domain paintings served by Wikimedia Commons (1000px thumbnails).
They are placeholders: the titles and attributions in this seed set are fictional.
"""
from decimal import Decimal

from seed_dev_images import IMAGES  # noqa: F401  (re-exported for seed_dev.py)

DEMO_USER_EMAIL = "demo.collector@seed.picasso.test"

ARTISTS = [
    dict(slug="meera-iyer", full_name="Meera Iyer", email="meera.iyer@seed.picasso.test",
         location="Chennai, India", primary_medium="Oil on canvas", years_practising=14, featured=True,
         instagram="meera.iyer.art",
         statement="I paint the light of South Indian coastlines and courtyards, working in layered oils "
                   "until a familiar street feels like a remembered dream."),
    dict(slug="rohan-deshmukh", full_name="Rohan Deshmukh", email="rohan.deshmukh@seed.picasso.test",
         location="Pune, India", primary_medium="Acrylic on canvas", years_practising=9, featured=False,
         instagram="rohan.paints",
         statement="Bold acrylics on everyday India: chai stalls, highways and night trains, "
                   "painted fast and loud."),
    dict(slug="ananya-bose", full_name="Ananya Bose", email="ananya.bose@seed.picasso.test",
         location="Kolkata, India", primary_medium="Watercolour", years_practising=11, featured=False,
         statement="Watercolour and gouache studies of monsoon cities, festival crowds and quiet gardens."),
    dict(slug="lucia-fernandez", full_name="Lucía Fernández", email="lucia.fernandez@seed.picasso.test",
         location="Madrid, Spain", primary_medium="Oil on canvas", years_practising=18, featured=True,
         website_url="https://example.com/lucia-fernandez",
         statement="Mediterranean light, domestic interiors and the slow hours of the afternoon, "
                   "painted in the tradition of the Spanish realists."),
    dict(slug="kenji-watanabe", full_name="Kenji Watanabe", email="kenji.watanabe@seed.picasso.test",
         location="Kyoto, Japan", primary_medium="Sumi ink on washi", years_practising=22, featured=True,
         statement="Sumi ink and mineral pigment on handmade washi. My subjects are rivers, bamboo and "
                   "water in every season."),
    dict(slug="hannah-whitfield", full_name="Hannah Whitfield", email="hannah.whitfield@seed.picasso.test",
         location="Bristol, United Kingdom", primary_medium="Charcoal on paper", years_practising=7,
         featured=False,
         statement="Charcoal drawings of estuaries, harbours and working hands, made on location."),
]

# Cover image per artist: the first image of their first artwork (set below).

_APPLICANT_BASE = [
    dict(email="sanjana.rao@seed.picasso.test", full_name="Sanjana Rao", location="Hyderabad, India",
         primary_medium="Photography", years_practising=5, instagram="sanjana.frames",
         statement="Street and architectural photography of old Hyderabad, printed on cotton rag paper.",
         work_titles=["Charminar Rain", "Old City Balcony", "Pearl Market Dusk"]),
    dict(email="tomas.herrera@seed.picasso.test", full_name="Tomás Herrera", location="Mexico City, Mexico",
         primary_medium="Mixed media", years_practising=8, instagram="tomas.herrera.studio",
         statement="Collage, oil and found paper layered into dense, colourful city maps.",
         work_titles=["Mapa de Coyoacán", "Paper Volcano", "Tianguis"]),
]

# Applicants reuse the tail of the image pool for their three sample works.
PENDING_APPLICANTS = []
for _n, _base in enumerate(_APPLICANT_BASE):
    _p = {k: v for k, v in _base.items() if k != "work_titles"}
    _p["works"] = [
        dict(title=t, year=2023, medium=_base["primary_medium"], dimensions="18 x 12 inches",
             image_url=IMAGES[-(1 + _n * 3 + k)][0])
        for k, t in enumerate(_base["work_titles"])
    ]
    PENDING_APPLICANTS.append(_p)


def _in(w, h):
    """Dimensions string plus structured cm values from inches."""
    return dict(dimensions=f"{w} x {h} inches",
                width_cm=str((Decimal(w) * Decimal("2.54")).quantize(Decimal("0.01"))),
                height_cm=str((Decimal(h) * Decimal("2.54")).quantize(Decimal("0.01"))))


# (artist, title, year, medium, category, listing_type, price, status, size, description)
# price: sale price, or starting price for auctions; None for display works.
_SPECS = [
    ("meera-iyer", "Monsoon Over Marina", 2023, "Oil on canvas", "painting", "sale", "185000", "published", (36, 24),
     "Grey surf and a bruised sky over Chennai's Marina Beach, just before the rain."),
    ("meera-iyer", "Temple Tank at Dusk", 2022, "Oil on canvas", "painting", "auction", "60000", "published", (30, 30),
     "A temple tank holding the last orange light of the day."),
    ("meera-iyer", "Jasmine Seller", 2021, "Oil on canvas", "painting", "sale", "72000", "published", (24, 30),
     "A flower seller threading jasmine in the early morning market."),
    ("meera-iyer", "Fishermen's Return", 2020, "Oil on canvas", "painting", "display", None, "published", (40, 28),
     "Boats pulled onto the sand as the catch is sorted. Part of the artist's own collection."),
    ("meera-iyer", "Kolam Morning", 2024, "Oil on canvas", "painting", "sale", "48000", "published", (20, 20),
     "A freshly drawn kolam at a doorstep, still wet from the morning wash."),
    ("meera-iyer", "The Quiet Verandah", 2019, "Oil on canvas", "painting", "display", None, "published", (30, 24),
     "An empty verandah in a Chettinad house, shutters half open."),
    ("rohan-deshmukh", "Highway Dreams", 2023, "Acrylic on canvas", "painting", "auction", "95000", "published", (48, 36),
     "A night truck on the Mumbai-Pune expressway streaked with colour."),
    ("rohan-deshmukh", "Neon Chai Stall", 2024, "Acrylic on canvas", "painting", "sale", "64000", "published", (30, 24),
     "A roadside chai stall glowing under strip lights."),
    ("rohan-deshmukh", "Ghats at Dawn", 2022, "Acrylic on canvas", "painting", "sale", "120000", "published", (36, 34),
     "Steps down to the river as the first pilgrims arrive."),
    ("rohan-deshmukh", "Rickshaw Constellation", 2024, "Acrylic on canvas", "painting", "auction", "45000", "published", (24, 24),
     "Auto-rickshaws parked in a lot, their lights drawn as a star map."),
    ("rohan-deshmukh", "Midnight Local", 2021, "Acrylic on canvas", "painting", "display", None, "published", (40, 20),
     "The last suburban train, nearly empty."),
    ("ananya-bose", "Rain on the Hooghly", 2023, "Watercolour on paper", "painting", "sale", "28000", "published", (22, 15),
     "Ferries crossing a rain-blurred river."),
    ("ananya-bose", "Durga Procession", 2022, "Watercolour on paper", "painting", "auction", "38000", "published", (22, 15),
     "Immersion day crowds carrying the goddess to the water."),
    ("ananya-bose", "Garden of Small Mercies", 2021, "Watercolour on paper", "painting", "sale", "22000", "sold", (15, 11),
     "A tangle of balcony plants after the first rain."),
    ("ananya-bose", "Terracotta Rooftops", 2020, "Watercolour on paper", "painting", "display", None, "published", (15, 11),
     "Old Kolkata rooftops under a pale sky."),
    ("ananya-bose", "Paper Boats", 2024, "Watercolour on paper", "painting", "sale", "18000", "published", (11, 8),
     "Children's paper boats racing down a flooded lane."),
    ("lucia-fernandez", "Olive Grove at Noon", 2022, "Oil on canvas", "painting", "sale", "340000", "published", (46, 32),
     "Silver-green olive trees baked in the midday heat of Andalusia."),
    ("lucia-fernandez", "Woman Reading, Toledo", 2023, "Oil on canvas", "painting", "auction", "150000", "published", (28, 36),
     "A woman reading by a window above the old city."),
    ("lucia-fernandez", "The Blue Courtyard", 2021, "Oil on canvas", "painting", "sale", "210000", "published", (32, 40),
     "A tiled patio in deep afternoon shade."),
    ("lucia-fernandez", "Café Tacón", 2019, "Oil on canvas", "painting", "display", None, "published", (30, 24),
     "Regulars at a Madrid café, caught between conversations."),
    ("lucia-fernandez", "Siesta", 2024, "Oil on canvas", "painting", "auction", "90000", "published", (36, 24),
     "Shuttered rooms and a sleeping cat."),
    ("kenji-watanabe", "Snow on the Kamo River", 2023, "Sumi ink on washi", "painting", "sale", "135000", "published", (36, 18),
     "Winter quiet along the Kamo, painted in a single sitting."),
    ("kenji-watanabe", "Bamboo in Wind", 2022, "Sumi ink on washi", "painting", "auction", "55000", "published", (40, 16),
     "Bamboo bending in a late-summer storm."),
    ("kenji-watanabe", "Fireflies, Late Summer", 2021, "Sumi ink and mineral pigment on washi", "painting", "sale", "98000", "sold", (30, 14),
     "A river bank at night, lit by fireflies."),
    ("kenji-watanabe", "Stone Bridge", 2020, "Sumi ink on washi", "painting", "display", None, "published", (28, 20),
     "An old stone bridge in morning mist."),
    ("kenji-watanabe", "Wave Study No. 4", 2024, "Sumi ink on washi", "drawing", "auction", "70000", "published", (24, 18),
     "The fourth of an ongoing series on the movement of water."),
    ("hannah-whitfield", "Estuary Light", 2023, "Charcoal on paper", "drawing", "sale", "42000", "published", (24, 18),
     "Low tide and a lifting fog on the Severn estuary."),
    ("hannah-whitfield", "Study of Hands", 2022, "Charcoal on paper", "drawing", "display", None, "published", (16, 12),
     "A potter's hands, drawn from life."),
    ("hannah-whitfield", "Winter Hedgerow", 2024, "Charcoal on paper", "drawing", "auction", "36000", "published", (20, 16),
     "Bare hawthorn and a low winter sun."),
    ("hannah-whitfield", "Harbour Cranes", 2021, "Charcoal on paper", "drawing", "display", None, "published", (22, 15),
     "Bristol's harbour cranes at the end of the working day."),
]

ARTWORKS = []
for _i, (_artist, _title, _year, _medium, _cat, _lt, _price, _status, _size, _desc) in enumerate(_SPECS):
    ARTWORKS.append(dict(
        artist=_artist, title=_title, year=_year, medium=_medium, category=_cat, listing_type=_lt,
        price=_price, sold_price=_price if _status == "sold" else None, status=_status,
        description=_desc, images=(_i % 3) + 1, **_in(*_size),
    ))
