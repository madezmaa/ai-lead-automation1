"""Validation and normalization helpers for incoming lead payloads.

Every value stored in the database is normalized here first, so downstream
code (rules, AI prompts, de-duplication) always sees consistent data.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

_WHITESPACE_RE = re.compile(r"\s+")
_NON_DIGITS_RE = re.compile(r"\D+")
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)

# ISO-3166 alpha-2 -> (canonical name, *aliases)
COUNTRY_DATA: dict[str, tuple[str, ...]] = {
    "US": ("United States", "usa", "united states of america", "america"),
    "CA": ("Canada",),
    "GB": ("United Kingdom", "uk", "great britain", "england", "britain"),
    "IE": ("Ireland",),
    "AU": ("Australia",),
    "NZ": ("New Zealand",),
    "DE": ("Germany", "deutschland"),
    "FR": ("France",),
    "NL": ("Netherlands", "nederland", "holland"),
    "BE": ("Belgium",),
    "CH": ("Switzerland", "schweiz"),
    "AT": ("Austria", "osterreich"),
    "SE": ("Sweden", "sverige"),
    "NO": ("Norway", "norge"),
    "DK": ("Denmark", "danmark"),
    "FI": ("Finland", "suomi"),
    "ES": ("Spain", "espana"),
    "IT": ("Italy", "italia"),
    "PT": ("Portugal",),
    "PL": ("Poland",),
    "SG": ("Singapore",),
    "AE": ("United Arab Emirates", "uae", "emirates"),
    "SA": ("Saudi Arabia",),
    "IL": ("Israel",),
    "IN": ("India",),
    "JP": ("Japan",),
    "KR": ("South Korea", "korea"),
    "CN": ("China",),
    "HK": ("Hong Kong",),
    "TW": ("Taiwan",),
    "MY": ("Malaysia",),
    "ID": ("Indonesia",),
    "TH": ("Thailand",),
    "PH": ("Philippines",),
    "VN": ("Vietnam",),
    "BR": ("Brazil", "brasil"),
    "MX": ("Mexico",),
    "AR": ("Argentina",),
    "CL": ("Chile",),
    "CO": ("Colombia",),
    "ZA": ("South Africa",),
    "NG": ("Nigeria",),
    "KE": ("Kenya",),
    "EG": ("Egypt",),
    "TR": ("Turkey", "turkiye"),
}

ISO2_TO_NAME: dict[str, str] = {code: names[0] for code, names in COUNTRY_DATA.items()}
COUNTRY_ALIASES: dict[str, str] = {
    alias.lower(): code for code, names in COUNTRY_DATA.items() for alias in names
}


def clean_text(value: str | None) -> str | None:
    """Unicode-normalize, strip and collapse internal whitespace."""
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    collapsed = _WHITESPACE_RE.sub(" ", normalized).strip()
    return collapsed or None


def normalize_email(value: str) -> str:
    """Lower-case, strip and syntactically validate an email address."""
    email = (clean_text(value) or "").lower()
    if len(email) > 320 or not EMAIL_RE.match(email):
        raise ValueError(f"Invalid email address: {value!r}")
    return email


def normalize_phone(value: str | None) -> str | None:
    """Normalize a phone number to E.164 (``+`` followed by 7-15 digits)."""
    raw = clean_text(value)
    if raw is None:
        return None
    digits = _NON_DIGITS_RE.sub("", raw)
    if raw.startswith("00") and len(digits) > 2:
        digits = digits[2:]
    elif len(digits) == 10 and not raw.startswith("+"):
        digits = f"1{digits}"  # NANP shortcut: bare 10-digit numbers are US/CA
    if not 7 <= len(digits) <= 15:
        raise ValueError(f"Invalid phone number: {value!r}")
    return f"+{digits}"


def normalize_name(value: str | None) -> str | None:
    """Collapse whitespace and title-case a person name."""
    raw = clean_text(value)
    if raw is None:
        return None
    return raw.title()


def normalize_source(value: str | None) -> str:
    """Lower-case a lead source and replace spaces with dashes."""
    raw = (clean_text(value) or "api").lower()
    slug = re.sub(r"[^a-z0-9_\-]+", "-", raw).strip("-")
    return slug or "api"


def normalize_website(value: str | None) -> str | None:
    """Add a scheme when missing and validate the host of a URL."""
    raw = clean_text(value)
    if raw is None:
        return None
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid website URL: {value!r}")
    host = parsed.netloc.split("@")[-1].split(":")[0]
    if "." not in host or host.startswith(".") or host.endswith("."):
        raise ValueError(f"Invalid website URL: {value!r}")
    return candidate.rstrip("/")


def normalize_country(value: str | None) -> tuple[str | None, str | None]:
    """Return ``(display_name, iso2_code)`` for a country input.

    Unknown values are kept as cleaned text with a ``None`` code so that a
    typo never loses information.
    """
    raw = clean_text(value)
    if raw is None:
        return None, None
    code = COUNTRY_ALIASES.get(raw.lower())
    if code is None and len(raw) == 2 and raw.isalpha():
        code = raw.upper()
    if code is not None:
        return ISO2_TO_NAME.get(code, raw), code
    return raw, None


def email_domain(email: str) -> str:
    """Return the lower-cased domain part of an email address."""
    return email.rsplit("@", 1)[-1].lower().strip(".")


# Domains/domains-with-suffixes reserved for testing and examples (RFC 2606 /
# RFC 6761). They are not deliverable mailboxes, so they must never be scored
# as if they were a real business email.
_RESERVED_EXAMPLE_DOMAINS = frozenset({"example.com", "example.org", "example.net"})
_RESERVED_EXAMPLE_TLDS = (".example", ".test", ".invalid", ".localhost", ".local")
_RESERVED_EXAMPLE_NAMES = frozenset({"example", "localhost", "test", "invalid"})


def is_free_email(email: str, free_domains: list[str] | tuple[str, ...]) -> bool:
    """True when the email uses a consumer mailbox provider."""
    return email_domain(email) in {d.lower() for d in free_domains}


def is_reserved_email_domain(email: str) -> bool:
    """True for RFC 2606 / RFC 6761 reserved (non-deliverable) example domains.

    Covers ``example.com``/``example.org``/``example.net`` and the reserved
    ``.example``, ``.test``, ``.invalid``, ``.localhost`` and ``.local`` TLDs so
    demo/test addresses like ``john@apexgrowth.example`` are never treated as a
    real business mailbox.
    """
    domain = email_domain(email)
    if not domain:
        return True
    if domain in _RESERVED_EXAMPLE_DOMAINS or domain in _RESERVED_EXAMPLE_NAMES:
        return True
    return domain.endswith(_RESERVED_EXAMPLE_TLDS)
