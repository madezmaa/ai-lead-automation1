"""Tests for validation and normalization of incoming leads."""

from __future__ import annotations

import pytest

from app.normalization import (
    email_domain,
    is_free_email,
    is_reserved_email_domain,
    normalize_country,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_website,
)
from app.schemas import LeadCreate


class TestEmail:
    def test_lowercases_and_strips(self) -> None:
        assert normalize_email("  JDoe@GMAIL.COM ") == "jdoe@gmail.com"

    @pytest.mark.parametrize(
        "value",
        ["not-an-email", "a@b", "user@", "@domain.com", "a b@c.com", "x@exa mple.com"],
    )
    def test_rejects_invalid(self, value: str) -> None:
        with pytest.raises(ValueError):
            normalize_email(value)


class TestPhone:
    def test_nanp_local(self) -> None:
        assert normalize_phone("(415) 555-2671") == "+14155552671"

    def test_international_with_plus(self) -> None:
        assert normalize_phone("+49 30 901820") == "+4930901820"

    def test_international_with_00(self) -> None:
        assert normalize_phone("0044 20 7946 0958") == "+442079460958"

    def test_none_passes_through(self) -> None:
        assert normalize_phone(None) is None

    @pytest.mark.parametrize("value", ["12345", "abc", "99999999999999999"])
    def test_rejects_invalid(self, value: str) -> None:
        with pytest.raises(ValueError):
            normalize_phone(value)


class TestName:
    def test_title_cases_and_collapses(self) -> None:
        assert normalize_name("  jane   DOE ") == "Jane Doe"

    def test_none(self) -> None:
        assert normalize_name(None) is None


class TestWebsite:
    def test_adds_scheme(self) -> None:
        assert normalize_website("example.com") == "https://example.com"

    def test_keeps_scheme(self) -> None:
        assert normalize_website("https://example.com/path") == "https://example.com/path"

    def test_none(self) -> None:
        assert normalize_website(None) is None

    @pytest.mark.parametrize(
        "value", ["not a url", "ftp://example.com", "ht!tp://x", ".example.com"]
    )
    def test_rejects_invalid(self, value: str) -> None:
        with pytest.raises(ValueError):
            normalize_website(value)


class TestCountry:
    def test_alias_lookup(self) -> None:
        assert normalize_country("united states") == ("United States", "US")

    def test_iso2(self) -> None:
        assert normalize_country("de") == ("Germany", "DE")

    def test_native_name(self) -> None:
        assert normalize_country("Deutschland") == ("Germany", "DE")

    def test_unknown_country_kept(self) -> None:
        assert normalize_country("Mordor") == ("Mordor", None)

    def test_none(self) -> None:
        assert normalize_country(None) == (None, None)


class TestFreeEmail:
    def test_free(self) -> None:
        assert is_free_email("x@gmail.com", ["gmail.com", "yahoo.com"])

    def test_business(self) -> None:
        assert not is_free_email("x@acme.io", ["gmail.com", "yahoo.com"])


class TestReservedEmailDomain:
    @pytest.mark.parametrize(
        "address",
        [
            "john@apexgrowth.example",
            "david@example.com",
            "a@example.org",
            "b@example.net",
            "c@service.test",
            "d@host.invalid",
            "e@dev.localhost",
            "f@box.local",
        ],
    )
    def test_reserved(self, address: str) -> None:
        assert is_reserved_email_domain(address)

    @pytest.mark.parametrize(
        "address",
        ["alex@acme.io", "x@gmail.com", "y@company.co.uk", "z@northstar.example.com.co"],
    )
    def test_not_reserved(self, address: str) -> None:
        assert not is_reserved_email_domain(address)

    def test_email_domain_helper(self) -> None:
        assert email_domain("John@ApexGrowth.Example") == "apexgrowth.example"


class TestLeadCreateSchema:
    def test_normalizes_known_fields(self, strong_payload: dict) -> None:
        lead = LeadCreate.model_validate(strong_payload)
        assert lead.email == "alex.johnson@acme.io"
        assert lead.first_name == "Alex"
        assert lead.phone == "+14155552671"
        assert lead.website == "https://acme.io"
        assert lead.country == "United States"
        assert lead.country_code == "US"
        assert lead.source == "referral"

    def test_normalizes_unknown_country(self) -> None:
        lead = LeadCreate.model_validate(
            {"email": "x@example.com", "country": "Atlantis", "country_code": "xx"}
        )
        assert lead.country == "Atlantis"
        assert lead.country_code is None

    def test_default_source_is_api(self) -> None:
        lead = LeadCreate.model_validate({"email": "x@example.com"})
        assert lead.source == "api"

    @pytest.mark.parametrize(
        "payload",
        [
            {"email": "not-an-email"},
            {"email": "x@example.com", "budget": -5},
            {"email": "x@example.com", "company_size": -1},
        ],
    )
    def test_invalid_payloads(self, payload: dict) -> None:
        with pytest.raises(ValueError):
            LeadCreate.model_validate(payload)
