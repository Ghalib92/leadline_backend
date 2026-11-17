"""A lead's score: the judge's fit plus need and reach counted from the listing."""

import pytest

from apps.ai.graphs.clients.scoring import lead_score, need_points, reach_points
from apps.ai.schemas.clients import Business


def biz(**fields):
    return Business(
        **{"name": "Biz", "source": "s", "source_label": "S", "source_url": "https://s/1"} | fields
    ).normalised()


@pytest.mark.parametrize(
    "fields, wanted, points",
    [
        ({"complete_listing": True}, {"no_website"}, 25),  # a sign the brief asks for
        ({"complete_listing": True}, {"no_chatbot"}, 12),  # a sign it doesn't name: half
        ({"complete_listing": True}, set(), 25),  # no signs named: all count
        ({"complete_listing": True, "website": "https://biz.ke"}, {"no_website"}, 0),
        ({"complete_listing": False}, {"no_website"}, 8),  # unknown, not "none"
        ({"website": "http://biz.ke", "complete_listing": True}, {"no_https"}, 12),
    ],
)
def test_need_comes_from_observed_signs(fields, wanted, points):
    business = biz(**fields)
    signals = business.observed_signals(busy_reviews=50)
    assert need_points(business, signals, wanted) == points


def test_reach_counts_ways_to_contact_and_activity():
    assert reach_points(biz(), busy_reviews=50) == 0
    busy = biz(phone="+254 700", email="a@b.ke", address="Moi Ave", reviews=300)
    assert reach_points(busy, busy_reviews=50) == 8 + 5 + 3 + 6


def test_the_judge_only_moves_fit():
    business = biz(complete_listing=True, phone="+254 700", reviews=120)
    signals = business.observed_signals(busy_reviews=50)
    generous = lead_score(business, signals, 100, {"no_website"}, busy_reviews=50)
    strict = lead_score(business, signals, 0, {"no_website"}, busy_reviews=50)
    assert generous == 40 + 25 + 14  # fit is capped at 40
    assert strict == 25 + 14


def site(**fields):
    from apps.ai.schemas.clients import SiteCheck

    return SiteCheck(**{"url": "https://biz.ke/"} | fields)


@pytest.mark.parametrize(
    "fields, status, points",
    [
        # No website listed, and a web search found none: likely, so a bit less than sure.
        ({"looked_up": True}, "not_found", 18),
        # Its site was found and checked and shows nothing: no visible need.
        (
            {"website": "https://biz.ke", "site": site(https=True, mobile_ready=True)},
            "listed",
            0,
        ),
        # Down: as strong a sign as no website at all.
        (
            {"website": "https://biz.ke", "site": site(reachable=False, problem="HTTP 500")},
            "down",
            25,
        ),
        # Old and not set up for phones: the signs add up, to the cap.
        (
            {
                "website": "https://biz.ke",
                "site": site(https=False, mobile_ready=False, copyright_year=2016, stale=True),
            },
            "listed",
            35,
        ),
    ],
)
def test_need_after_a_lookup(fields, status, points):
    business = biz(**fields)
    assert business.website_status() == status
    signals = business.observed_signals(busy_reviews=50)
    assert need_points(business, signals, set()) == points


def test_a_website_found_by_search_is_marked_found():
    business = biz(website="https://biz.ke", found_on={"website": "web search"})
    assert business.website_status() == "found"


def test_missing_features_count_only_when_the_brief_asks():
    business = biz(website="https://biz.ke", site=site(booking=False, chat=False))
    assert business.observed_signals(busy_reviews=50) == ["no_online_booking", "no_chatbot"]
    assert business.observed_signals(busy_reviews=50, wanted={"no_chatbot", "no_website"}) == [
        "no_chatbot"
    ]
