"""Business directories (OpenStreetMap, Google Maps through Apify) on a fake web."""

import json

import httpx
import pytest
from django.core.cache import cache

from apps.ai.config.loader import load_sources_file
from apps.ai.schemas.clients import Business, social_kind
from apps.ai.tools.businesses.base import BusinessQuery, SearchError, SourceOff
from apps.ai.tools.businesses.registry import build_business_source

NAIROBI = [{"boundingbox": ["-1.44", "-1.16", "36.66", "37.10"], "display_name": "Nairobi"}]
HOTELS = {
    "elements": [
        {
            "type": "node",
            "id": 1,
            "tags": {
                "name": "Savannah Lodge",
                "tourism": "hotel",
                "phone": "+254 700 000001;+254 700 000002",
                "website": "savannahlodge.co.ke",
                "addr:street": "Ngong Road",
                "addr:housenumber": "12",
            },
        },
        {
            "type": "way",
            "id": 2,
            "center": {"lat": -1.2, "lon": 36.8},
            "tags": {
                "name": "Baobab Inn",
                "tourism": "guest_house",
                "website": "https://www.instagram.com/baobabinn",
            },
        },
        {"type": "node", "id": 3, "tags": {"tourism": "hotel"}},  # no name: dropped
        {"type": "node", "id": 4, "tags": {"name": "Old Hotel", "disused": "yes"}},
    ]
}


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def source(name, **settings_overrides):
    return build_business_source(name, load_sources_file().sources[name])


def query(**overrides):
    return BusinessQuery(
        **{"category": "hospitality", "keyword": "", "place": "Nairobi", "country": "KE"}
        | overrides
    )


# --- OpenStreetMap ------------------------------------------------------------------------


@pytest.fixture
def osm_web(web):
    web.route(
        "nominatim.openstreetmap.org",
        handler=lambda request: httpx.Response(200, json=NAIROBI),
    )
    web.route(
        "overpass-api.de",
        handler=lambda request: httpx.Response(200, json=HOTELS),
    )
    return web


def test_osm_finds_named_businesses_with_their_listed_facts(osm_web):
    businesses = source("osm_places").search(query(), max_results=10)
    assert [b.name for b in businesses] == ["Savannah Lodge", "Baobab Inn"]
    lodge, inn = businesses
    assert lodge.phone == "+254 700 000001"  # the first of several
    assert lodge.website == "https://savannahlodge.co.ke"  # scheme added
    assert lodge.address == "12 Ngong Road"
    assert lodge.city == "Nairobi" and lodge.country == "KE"
    assert lodge.source_url == "https://www.openstreetmap.org/node/1"
    assert lodge.complete_listing is False
    # An Instagram profile given as the website is a social profile, not a site.
    assert inn.website == ""
    assert inn.socials == {"instagram": "https://www.instagram.com/baobabinn"}


def test_osm_asks_for_the_kinds_tags_inside_the_place(osm_web):
    source("osm_places").search(query(), max_results=10)
    geocode, overpass = osm_web.requests
    assert geocode.url.params["countrycodes"] == "ke"
    sent = httpx.QueryParams(overpass.content.decode())["data"]
    assert '"tourism"~"^(hotel|guest_house|motel|hostel|apartment)$"' in sent
    assert "(-1.44,36.66,-1.16,37.10)" in sent


def test_osm_geocodes_a_place_once(osm_web):
    osm = source("osm_places")
    osm.search(query(), max_results=5)
    osm.search(query(category="restaurants"), max_results=5)
    hosts = [request.url.host for request in osm_web.requests]
    assert hosts.count("nominatim.openstreetmap.org") == 1


def test_osm_tries_the_next_server_when_one_is_busy(web):
    web.route("nominatim.openstreetmap.org", handler=lambda r: httpx.Response(200, json=NAIROBI))
    web.route("overpass-api.de", handler=lambda r: httpx.Response(502, text="busy"))
    web.route("overpass.kumi.systems", handler=lambda r: httpx.Response(504, text="busy"))
    web.route("maps.mail.ru", handler=lambda r: httpx.Response(200, json=HOTELS))
    assert len(source("osm_places").search(query(), max_results=10)) == 2
    assert web.count("overpass.private.coffee") == 0  # stops at the first answer


def test_osm_says_when_every_server_is_busy(web):
    web.route("nominatim.openstreetmap.org", handler=lambda r: httpx.Response(200, json=NAIROBI))
    web.route("overpass-api.de", handler=lambda r: httpx.Response(504, text="busy"))
    web.route("overpass.kumi.systems", handler=lambda r: httpx.Response(502, text="busy"))
    web.route("maps.mail.ru", handler=lambda r: httpx.Response(429, text="slow down"))
    web.route("overpass.private.coffee", handler=lambda r: httpx.Response(503, text="down"))
    with pytest.raises(SearchError, match="busy"):
        source("osm_places").search(query(), max_results=10)


def test_osm_unknown_place(web):
    web.route("nominatim.openstreetmap.org", handler=lambda r: httpx.Response(200, json=[]))
    with pytest.raises(SearchError, match="does not know the place"):
        source("osm_places").search(query(place="Atlantis"), max_results=10)


def test_osm_keyword_search_strips_query_syntax(osm_web):
    osm = source("osm_places")
    assert osm.supports(query(category="other", keyword='spa"];out;'))
    osm.search(query(category="other", keyword='spa"];out;'), max_results=5)
    sent = httpx.QueryParams(osm_web.requests[-1].content.decode())["data"]
    assert '"name"~"spaout",i' in sent
    assert not osm.supports(query(category="other", keyword="!!!"))


# --- Google Maps through Apify ---------------------------------------------------------------

PLACES = [
    {
        "title": "Kilima Clinic",
        "categoryName": "Medical clinic",
        "address": "Moi Avenue, Nairobi",
        "city": "Nairobi",
        "countryCode": "KE",
        "phone": "+254 711 111111",
        "website": "http://kilimaclinic.co.ke",
        "totalScore": 4.6,
        "reviewsCount": 230,
        "url": "https://www.google.com/maps/place/kilima",
        "emails": ["hello@kilimaclinic.co.ke"],
    },
    {"title": "Closed Spa", "permanentlyClosed": True},
    {"title": "Tausi Salon", "url": "https://www.google.com/maps/place/tausi", "reviewsCount": 4},
]


def test_google_maps_is_off_unless_switched_on(settings):
    settings.APIFY_GOOGLE_MAPS_ENABLED = False
    with pytest.raises(SourceOff):
        source("google_maps")


def test_google_maps_needs_a_token(settings):
    settings.APIFY_GOOGLE_MAPS_ENABLED = True
    settings.APIFY_API_TOKEN = ""
    with pytest.raises(SearchError, match="APIFY_API_TOKEN"):
        source("google_maps")


def test_google_maps_lists_open_businesses(settings, web):
    settings.APIFY_GOOGLE_MAPS_ENABLED = True
    settings.APIFY_API_TOKEN = "apify-test"
    web.route("api.apify.com", handler=lambda r: httpx.Response(201, json=PLACES))
    businesses = source("google_maps").search(query(category="health"), max_results=10)
    assert [b.name for b in businesses] == ["Kilima Clinic", "Tausi Salon"]
    clinic = businesses[0]
    assert clinic.complete_listing is True
    assert (clinic.rating, clinic.reviews, clinic.email) == (4.6, 230, "hello@kilimaclinic.co.ke")
    sent = web.requests[0]
    assert sent.headers["Authorization"] == "Bearer apify-test"
    body = json.loads(sent.content)
    assert body["searchStringsArray"] == ["clinics"]
    assert body["locationQuery"] == "Nairobi, KE"
    assert body["maxCrawledPlacesPerSearch"] == 10


def test_google_maps_without_credit(settings, web):
    settings.APIFY_GOOGLE_MAPS_ENABLED = True
    settings.APIFY_API_TOKEN = "apify-test"
    web.route("api.apify.com", handler=lambda r: httpx.Response(402, json={}))
    with pytest.raises(SearchError, match="no credit"):
        source("google_maps").search(query(), max_results=5)


# --- What a listing shows ----------------------------------------------------------------------


def business(**fields):
    return Business(
        **{"name": "Biz", "source": "s", "source_label": "S", "source_url": "https://s/1"} | fields
    ).normalised()


@pytest.mark.parametrize(
    "fields, signals",
    [
        ({"complete_listing": True}, ["no_website"]),
        ({"complete_listing": False}, []),  # not mapped is not "no website"
        ({"website": "wa.me/254700000000", "complete_listing": True}, ["social_only"]),
        ({"website": "http://biz.co.ke"}, ["no_https"]),
        ({"website": "https://biz.co.ke", "reviews": 80}, ["many_reviews"]),
        ({"website": "https://biz.co.ke", "reviews": 10}, []),
    ],
)
def test_observed_signals_are_facts_not_guesses(fields, signals):
    assert business(**fields).observed_signals(busy_reviews=50) == signals


def test_social_kinds():
    assert social_kind("https://m.facebook.com/biz") == "facebook"
    assert social_kind("linktr.ee/biz") == "linktree"
    assert social_kind("https://biz.co.ke") == ""
