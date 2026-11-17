"""Phone numbers in international form, for WhatsApp click-to-chat (wa.me) links.

Directories and websites write numbers locally ("0722 123 456") or internationally
("+254 722 123 456"). wa.me needs the international digits with no "+". A number
that can't be placed with certainty gives "" rather than a guess, since a wrong
number would message a stranger.
"""

# Calling codes and national number lengths (without the trunk 0) for the countries
# Leadline's users sell in. Add a country here before its local numbers can be used.
COUNTRIES = {
    "KE": ("254", (9,)),
    "UG": ("256", (9,)),
    "TZ": ("255", (9,)),
    "RW": ("250", (9,)),
    "ET": ("251", (9,)),
    "NG": ("234", (10,)),
    "GH": ("233", (9,)),
    "ZA": ("27", (9,)),
    "GB": ("44", (10,)),
    "US": ("1", (10,)),
}
MIN_DIGITS, MAX_DIGITS = 8, 15


def international_digits(phone: str, country: str = "") -> str:
    """'0722 123 456' (KE) → '254722123456'; '+254 20 722 9000' → '254207229000'."""
    text = (phone or "").split("/")[0].split(",")[0].strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return ""
    if text.startswith("+"):
        return digits if MIN_DIGITS <= len(digits) <= MAX_DIGITS else ""
    if digits.startswith("00"):
        digits = digits[2:]
        return digits if MIN_DIGITS <= len(digits) <= MAX_DIGITS else ""
    code, lengths = COUNTRIES.get(country.upper(), ("", ()))
    if not code:
        return ""
    if digits.startswith(code) and len(digits) - len(code) in lengths:
        return digits  # written internationally without the "+"
    national = digits[1:] if digits.startswith("0") else digits
    return code + national if len(national) in lengths else ""
