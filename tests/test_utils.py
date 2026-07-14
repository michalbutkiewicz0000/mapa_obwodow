import pandas as pd

from utils import normalize_obwod, normalize_teryt, parse_int, short_party_name


def test_normalize_teryt_strips_quotes_and_trailing_zero():
    assert normalize_teryt('"126101.0"') == "126101"
    assert normalize_teryt("126101") == "126101"


def test_normalize_teryt_nan_returns_empty_string():
    assert normalize_teryt(float("nan")) == ""


def test_normalize_obwod_parses_float_string():
    assert normalize_obwod("5.0") == 5
    assert normalize_obwod(5) == 5


def test_normalize_obwod_nan_returns_none():
    assert normalize_obwod(float("nan")) is None


def test_normalize_obwod_empty_string_returns_none():
    assert normalize_obwod("") is None


def test_short_party_name_known_committees():
    assert short_party_name("KOMITET WYBORCZY PRAWO I SPRAWIEDLIWOŚĆ") == "PiS"
    assert short_party_name("KOALICJA OBYWATELSKA PO .N IPL ZIELONI") == "KO"


def test_short_party_name_unknown_committee_strips_prefix():
    result = short_party_name("KOMITET WYBORCZY WYBORCÓW JAN KOWALSKI")
    assert "Komitet Wyborczy" not in result


def test_parse_int_handles_spaces_and_nan():
    assert parse_int("1 234") == 1234
    assert parse_int(float("nan")) == 0
    assert parse_int("") == 0
    assert parse_int("42") == 42
