import pandas as pd

from build_gminy import aggregate_by_gmina


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
