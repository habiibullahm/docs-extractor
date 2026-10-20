import pytest

from docex.normalize import digits_only, normalize_currency, parse_date, parse_number, parse_time


@pytest.mark.parametrize("raw,expected", [
    ("Rp 1.250.000,50", 1250000.50),
    ("1.250.000,00", 1250000.0),
    ("1.250.000", 1250000.0),
    ("12.500", 12500.0),
    ("1,250,000.50", 1250000.50),
    ("60,000", 60000.0),
    ("45,5", 45.5),
    ("(5.000)", -5000.0),
    ("-95.100", -95100.0),
    ("Rp1.000.000,-", 1000000.0),
    (1500, 1500.0),
    ("", None),
    (None, None),
    ("abc", None),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("12 Januari 2025", "2025-01-12"),
    ("12/01/25", "2025-01-12"),
    ("12/01/2025", "2025-01-12"),
    ("2025-01-12", "2025-01-12"),
    ("03 Mei 2024", "2024-05-03"),
    ("9 Agu 2023", "2023-08-09"),
    ("17 Ags 2023", "2023-08-17"),
    ("1 Okt 2024", "2024-10-01"),
    ("25 Des 2024", "2024-12-25"),
    ("Senin, 5 Februari 2024", "2024-02-05"),
    ("Jan 12, 2025", "2025-01-12"),
    ("31/02/2025", None),
    ("tidak ada", None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_ids_and_misc():
    assert digits_only("01.234.567.8-901.234") == "012345678901234"
    assert digits_only(None) is None
    assert parse_time("14.05") == "14:05"
    assert parse_time("25:00") is None
    assert normalize_currency("Rp") == "IDR"
    assert normalize_currency("usd") == "USD"
