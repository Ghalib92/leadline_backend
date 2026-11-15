"""A lead's score: the client judge's customer fit plus what the listing itself shows.

    fit    0–40  the judge: the kind, size and place of business the brief targets
    need   0–35  observed signals (no website on a complete listing, social only, no
                 HTTPS); full weight when the brief names the sign, half otherwise
    reach  0–25  a way to contact it and signs it is active (phone, email, rating…)

Need and reach are counted here, from facts, so a lenient or a strict model moves
only the part it is asked to judge.
"""

from apps.ai.schemas.clients import Business

NEED_WEIGHTS = {"no_website": 25, "social_only": 25, "no_https": 12}
# A complete listing showing none of the signs: no visible need. An incomplete one
# (OpenStreetMap): unknown, so a little.
NO_SIGN_COMPLETE, NO_SIGN_UNKNOWN = 0, 8
NEED_MAX, REACH_MAX, FIT_MAX = 35, 25, 40


def need_points(business: Business, signals: list[str], wanted: set[str]) -> int:
    weighted = [
        NEED_WEIGHTS[signal] if signal in wanted or not wanted else NEED_WEIGHTS[signal] // 2
        for signal in signals
        if signal in NEED_WEIGHTS
    ]
    if not weighted:
        return NO_SIGN_COMPLETE if business.complete_listing else NO_SIGN_UNKNOWN
    return min(NEED_MAX, sum(weighted))


def reach_points(business: Business, *, busy_reviews: int) -> int:
    points = 0
    points += 8 if business.phone else 0
    points += 5 if business.email else 0
    points += 3 if business.socials else 0
    points += 3 if business.address else 0
    if business.reviews:
        points += 6 if business.reviews >= busy_reviews else 3
    return min(REACH_MAX, points)


def lead_score(
    business: Business, signals: list[str], fit: int, wanted: set[str], *, busy_reviews: int
) -> int:
    fit = max(0, min(FIT_MAX, fit))
    return (
        fit
        + need_points(business, signals, wanted)
        + reach_points(business, busy_reviews=busy_reviews)
    )
