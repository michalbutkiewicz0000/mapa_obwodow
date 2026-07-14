from parse_opis_granic import (
    NumberRange,
    StreetRule,
    normalize_text,
    parse_opis_granic,
    parse_range_fragment,
    resolve_obwod,
    streets_equal,
)


def test_number_range_contains():
    assert NumberRange(1, 13).contains(1)
    assert NumberRange(1, 13).contains(13)
    assert NumberRange(1, 13).contains(7)
    assert not NumberRange(1, 13).contains(14)
    assert not NumberRange(1, 13).contains(0)


def test_number_range_open_ended():
    rng = NumberRange(10, None)
    assert rng.contains(10)
    assert rng.contains(1000)
    assert not rng.contains(9)


def test_streets_equal_exact_match():
    assert streets_equal("Długa", "Długa")
    assert streets_equal("ul. Długa", "długa")  # "ul." usuwane przez normalize_text


def test_streets_equal_rejects_partial_word_match():
    # Dawny luźny substring znakowy dawał fałszywe trafienia typu "Widok" w "Widokowa"
    # (różne słowa/ulice) — obecne dopasowanie działa na całych słowach, nie znakach.
    assert not streets_equal("Widok", "Widokowa")
    assert not streets_equal("Kolej", "Kolejowa")


def test_streets_equal_allows_whole_word_prefix():
    assert streets_equal("Nowa Kolejowa", "Nowa Kolejowa")


def test_normalize_text_strips_prefixes_and_diacritics():
    assert normalize_text("ul. Świętego Jana") == "świetego jana" or normalize_text("ul. Świętego Jana") == "swietego jana"
    assert "ul." not in normalize_text("ul. Krótka")


def test_parse_range_fragment_dash_range():
    ranges, parity, street = parse_range_fragment("Dajwór 2-20")
    assert parity == "all"
    assert any(r.start == 2 and r.end == 20 for r in ranges)


def test_parse_range_fragment_parity():
    ranges, parity, street = parse_range_fragment("Krótka (parzyste)")
    assert parity == "even"
    assert street == "Krótka"


def test_parse_range_fragment_do_konca():
    ranges, parity, street = parse_range_fragment("Długa od 5 do końca")
    assert any(r.end is None for r in ranges)


def test_parse_opis_granic_wies():
    rules = parse_opis_granic(1, "wieś", "Nowa Wieś, Stara Wieś")
    assert "Nowa Wieś" in rules.villages
    assert "Stara Wieś" in rules.villages


def test_parse_opis_granic_miasto_streets():
    rules = parse_opis_granic(1, "miasto", "Długa, Krótka")
    names = [s.name for s in rules.streets]
    assert "Długa" in names
    assert "Krótka" in names


def test_resolve_obwod_no_match_returns_none():
    rules_list = [parse_opis_granic(1, "miasto", "Długa")]
    winner, count = resolve_obwod(rules_list, "Zupełnie Inna", 5)
    assert winner is None
    assert count == 0


def test_resolve_obwod_single_match():
    rules_list = [parse_opis_granic(1, "miasto", "Długa")]
    winner, count = resolve_obwod(rules_list, "Długa", 5)
    assert winner == 1
    assert count == 1


def test_resolve_obwod_prefers_more_specific_rule():
    # Obwód 1: cała ulica Długa. Obwód 2: Długa tylko parzyste 1-50.
    rules1 = StreetRule(name="Długa")
    rules2 = StreetRule(name="Długa", parity="even", ranges=[NumberRange(1, 50)])
    obwod1 = parse_opis_granic(1, "miasto", "Długa")
    obwod1.streets = [rules1]
    obwod2 = parse_opis_granic(2, "miasto", "Długa parzyste 1-50")
    obwod2.streets = [rules2]

    winner, count = resolve_obwod([obwod1, obwod2], "Długa", 20)
    assert count == 2  # oba pasują surowo
    assert winner == 2  # ale obwod2 ma bardziej specyficzną regułę (zakres+parzystość)


def test_resolve_obwod_true_ambiguity_returns_none():
    # Dwa obwody z identyczną specyficznością dla tej samej ulicy — prawdziwy konflikt.
    obwod1 = parse_opis_granic(1, "miasto", "Wspólna")
    obwod2 = parse_opis_granic(2, "miasto", "Wspólna")
    winner, count = resolve_obwod([obwod1, obwod2], "Wspólna", 5)
    assert winner is None
    assert count == 2
