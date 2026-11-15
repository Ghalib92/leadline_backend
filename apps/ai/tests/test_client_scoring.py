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
