"""Regression: extreme Jahres-Parameter (year=0 / 999999) dürfen in KEINEM
year-nehmenden GET-Endpoint einen unbehandelten 500 auslösen.

Das ungeprüfte Jahr floss in ``date(year, month, 1)`` → ``ValueError: year must be
in 1..9999``. Geschlossen durch (a) einen zentralen ValueError-Handler in main.py,
der out-of-range Datums-/Jahr-Fehler auf 400 mappt (statt generischem 500), und
(b) einen expliziten Vorab-Jahres-Check in ``/api/export/statistics``, dessen breiter
``except Exception``-Block die ValueError sonst als 500 „Datenbankfehler" verschleiert.
Über den OpenAPI-Sweep als 16-Endpunkt-Klasse gefunden; hier eine repräsentative,
parameter-stabile Auswahl."""
import pytest
from starlette.testclient import TestClient

# Repräsentative Auswahl der Klasse — alle liefern bei year=2026 einen sauberen 200
# (Parameter bewusst minimal & stabil gehalten).
CLASS_ENDPOINTS = [
    "/api/statistics?year={y}&month=1",
    "/api/statistics/year-summary?year={y}",
    "/api/export/statistics?year={y}",
    "/api/warnings?year={y}",
    "/api/zeitkonto?year={y}",
    "/api/dashboard/summary?year={y}",
]


@pytest.mark.parametrize("y", [0, 999999])
@pytest.mark.parametrize("tmpl", CLASS_ENDPOINTS)
def test_extreme_year_is_4xx_not_500(sync_client: TestClient, tmpl: str, y: int):
    st = sync_client.get(tmpl.format(y=y)).status_code
    assert 400 <= st < 500, f"{tmpl.format(y=y)} -> {st}"


@pytest.mark.parametrize("tmpl", CLASS_ENDPOINTS)
def test_valid_year_still_ok(sync_client: TestClient, tmpl: str):
    assert sync_client.get(tmpl.format(y=2026)).status_code == 200


class TestDateDomainMatcher:
    """Der ValueError→400-Matcher greift NUR bei Datums-Domänen-Fehlern; jeder andere
    ValueError liefert None und läuft weiter in den generischen 500-Handler (keine
    Verschleierung echter Bugs)."""

    @pytest.mark.parametrize(
        "msg",
        [
            "year must be in 1..9999, not 0",
            "year must be in 1..9999, not 999999",
            "year is out of range",
            "month must be in 1..12, not 13",
        ],
    )
    def test_date_domain_matched(self, msg: str):
        from sp5api.main import _date_domain_value_error

        assert _date_domain_value_error(ValueError(msg)) is not None

    @pytest.mark.parametrize(
        "msg",
        [
            "invalid literal for int() with base 10: 'x'",
            "not enough values to unpack (expected 2, got 1)",
            "could not convert string to float: 'abc'",
        ],
    )
    def test_non_date_value_error_not_matched(self, msg: str):
        from sp5api.main import _date_domain_value_error

        assert _date_domain_value_error(ValueError(msg)) is None
