import pandas as pd

from build_gminy import aggregate_by_gmina, aggregate_by_powiat, aggregate_by_wojewodztwo


def test_aggregate_by_gmina_sums_votes_and_weights_turnout():
    results = pd.DataFrame(
        [
            {
                "teryt": "126101",
                "obwod": 1,
                "eligible": 1000,
                "voted": 800,
                "glosy_wazne": 790,
                "winner": "KO",
                "results": {"KO": 500, "PiS": 290},
            },
            {
                "teryt": "126101",
                "obwod": 2,
                "eligible": 500,
                "voted": 300,
                "glosy_wazne": 295,
                "winner": "PiS",
                "results": {"KO": 100, "PiS": 195},
            },
        ]
    )

    aggregated = aggregate_by_gmina(results)
    row = aggregated[aggregated["teryt"] == "126101"].iloc[0]

    # Frekwencja ważona: suma głosujących / suma uprawnionych, nie średnia arytmetyczna.
    assert row["frekwencja"] == round((800 + 300) / (1000 + 500), 4)
    assert row["glosy_wazne"] == 790 + 295
    assert row["results"] == {"KO": 600, "PiS": 485}
    assert row["winner"] == "KO"
    assert row["obwody"] == 2


def test_aggregate_by_gmina_pads_short_teryt():
    # PKW zapisuje TERYT bez wiodącego zera dla jednocyfrowych województw.
    results = pd.DataFrame(
        [
            {
                "teryt": "20101",
                "obwod": 1,
                "eligible": 100,
                "voted": 50,
                "glosy_wazne": 48,
                "winner": "KO",
                "results": {"KO": 48},
            }
        ]
    )
    aggregated = aggregate_by_gmina(results)
    assert aggregated.iloc[0]["teryt"] == "020101"


def test_aggregate_by_gmina_drops_rows_without_teryt():
    results = pd.DataFrame(
        [
            {"teryt": None, "obwod": 1, "eligible": 100, "voted": 50, "glosy_wazne": 48, "winner": "KO", "results": {}},
        ]
    )
    aggregated = aggregate_by_gmina(results)
    assert aggregated.empty


def test_aggregate_by_gmina_merges_warszawa_dzielnice():
    # PKW liczy Warszawę jako 18 osobnych "gmin" (dzielnic) — powinny się
    # zsumować pod jednym kodem całego miasta (146501), zgodnym z granicami
    # gmin i punktami adresowymi PRG.
    results = pd.DataFrame(
        [
            {
                "teryt": "146502",  # Bemowo
                "obwod": 1,
                "eligible": 1000,
                "voted": 700,
                "glosy_wazne": 690,
                "winner": "KO",
                "results": {"KO": 400, "PiS": 290},
            },
            {
                "teryt": "146510",  # Śródmieście
                "obwod": 1,
                "eligible": 2000,
                "voted": 1800,
                "glosy_wazne": 1780,
                "winner": "KO",
                "results": {"KO": 1200, "PiS": 580},
            },
        ]
    )
    aggregated = aggregate_by_gmina(results)
    assert len(aggregated) == 1
    row = aggregated.iloc[0]
    assert row["teryt"] == "146501"
    assert row["obwody"] == 2
    assert row["glosy_wazne"] == 690 + 1780
    assert row["results"] == {"KO": 1600, "PiS": 870}


def test_aggregate_by_powiat_groups_by_four_digit_prefix():
    # Dwie różne gminy (020101, 020102) w tym samym powiecie (prefiks "0201")
    # powinny się zsumować pod jednym kodem powiatu — bez osobnego mapowania
    # dzielnic Warszawy: "146502"[:4] == "146501"[:4] == "1465", więc powiat
    # naturalnie scala je tak samo jak jawny remap w aggregate_by_gmina.
    results = pd.DataFrame(
        [
            {
                "teryt": "020101",
                "obwod": 1,
                "eligible": 1000,
                "voted": 600,
                "glosy_wazne": 590,
                "winner": "KO",
                "results": {"KO": 400, "PiS": 190},
            },
            {
                "teryt": "020102",
                "obwod": 1,
                "eligible": 500,
                "voted": 300,
                "glosy_wazne": 295,
                "winner": "PiS",
                "results": {"KO": 100, "PiS": 195},
            },
        ]
    )
    aggregated = aggregate_by_powiat(results)
    assert len(aggregated) == 1
    row = aggregated.iloc[0]
    assert row["teryt"] == "0201"
    assert row["obwody"] == 2
    assert row["results"] == {"KO": 500, "PiS": 385}


def test_aggregate_by_wojewodztwo_groups_by_two_digit_prefix():
    results = pd.DataFrame(
        [
            {
                "teryt": "126101",
                "obwod": 1,
                "eligible": 1000,
                "voted": 600,
                "glosy_wazne": 590,
                "winner": "KO",
                "results": {"KO": 400, "PiS": 190},
            },
            {
                "teryt": "121101",
                "obwod": 1,
                "eligible": 500,
                "voted": 300,
                "glosy_wazne": 295,
                "winner": "KO",
                "results": {"KO": 200, "PiS": 95},
            },
        ]
    )
    aggregated = aggregate_by_wojewodztwo(results)
    assert len(aggregated) == 1
    assert aggregated.iloc[0]["teryt"] == "12"
    assert aggregated.iloc[0]["obwody"] == 2
