"""Alertas, informes periódicos y tools asociadas (lógica pura + tools con mocks)."""
import datetime
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402
from cumulusmx_mcp import alerts, reporting  # noqa: E402

D = datetime.date


def kinds(result):
    return {(a["tipo"], a["nivel"]) for a in result}


class TestEvaluateAlerts:
    def test_calm_conditions_raise_nothing(self):
        assert alerts.evaluate_alerts({"temp": 15.0, "dew": 8.0, "hum": 60, "wgust": 12, "rrate": 0, "UV": 3, "presstrend": 0.2}) == []

    def test_frost_levels(self):
        assert kinds(alerts.evaluate_alerts({"temp": 2.5})) == {("helada", "aviso")}
        assert kinds(alerts.evaluate_alerts({"temp": -1.0})) == {("helada", "alerta")}
        assert kinds(alerts.evaluate_alerts({"temp": 8.0, "tempTL": -2.0})) == {("helada", "alerta")}   # ya ha helado hoy
        assert kinds(alerts.evaluate_alerts({"temp": 4.0})) == set()
        assert "helada blanca" in alerts.evaluate_alerts({"temp": 2.0, "dew": 0.5})[0]["mensaje"]

    def test_wind_rain_heat_uv_pressure(self):
        out = alerts.evaluate_alerts({"temp": 30, "wgust": 95, "rrate": 45, "heatindex": 43, "UV": 11, "presstrend": -4.5})
        assert kinds(out) == {("viento", "alerta"), ("lluvia intensa", "alerta"), ("calor", "alerta"), ("UV", "alerta"),
                              ("presión", "aviso")}
        out = alerts.evaluate_alerts({"wgust": 65, "rrate": 21, "heatindex": 36, "UV": 8})
        assert kinds(out) == {("viento", "aviso"), ("lluvia intensa", "aviso"), ("calor", "aviso"), ("UV", "aviso")}

    def test_gust_uses_the_highest_of_gust_and_latest(self):
        assert alerts.evaluate_alerts({"wgust": 10, "wlatest": 70})[0]["valor"] == 70

    def test_heat_falls_back_to_temperature(self):
        assert kinds(alerts.evaluate_alerts({"temp": 37.0})) == {("calor", "aviso")}

    def test_dry_hot_air_and_stopped_station(self):
        out = alerts.evaluate_alerts({"temp": 31, "hum": 15, "data_stopped": True})
        assert ("sequedad", "info") in kinds(out) and ("estación", "alerta") in kinds(out)
        assert alerts.evaluate_alerts({"temp": 20, "hum": 10}) == []     # seco pero sin calor

    def test_results_are_sorted_by_severity(self):
        levels = [a["nivel"] for a in alerts.evaluate_alerts({"temp": 31, "hum": 15, "wgust": 65, "rrate": 60})]
        assert levels == sorted(levels, key={"alerta": 0, "aviso": 1, "info": 2}.get)

    def test_custom_thresholds(self):
        assert kinds(alerts.evaluate_alerts({"wgust": 35}, {"wind_gust": 30})) == {("viento", "aviso")}
        assert alerts.evaluate_alerts({"wgust": 65}, {"wind_gust": 100}) == []
        with pytest.raises(ValueError, match="umbral desconocido"):
            alerts.merge_thresholds({"nope": 1})


TAGS = {"temp": "2.0", "dew": "0.4", "hum": "90", "heatindex": "2.0", "wgust": "70", "wlatest": "20", "rrate": "0",
        "UV": "0", "presstrend": "-0.5", "tempTL": "1.5", "DataStopped": "0", "tempunit": "&#176;C", "windunit": "km/h",
        "rainunit": "mm", "pressunit": "hPa", "time": "05:40", "date": "11/11/26"}


class TestWeatherAlertsTool:
    @pytest.mark.asyncio
    async def test_reads_tags_with_plain_numbers_and_reports(self):
        with patch.object(server, "_get", new=AsyncMock(return_value=json.dumps(TAGS))) as g:
            res = json.loads(await server.weather_alerts())
        assert g.await_args.args[0].startswith("/api/tags/process.json?rc&temp&")
        assert res["hay_alertas"] is True and {a["tipo"] for a in res["alertas"]} == {"helada", "viento"}
        assert res["condiciones"]["temp"] == 2.0 and res["avisos"] == [] and res["momento"] == "05:40"

    @pytest.mark.asyncio
    async def test_warns_about_non_metric_units_and_accepts_thresholds(self):
        tags = dict(TAGS, tempunit="&#176;F", windunit="mph")
        with patch.object(server, "_get", new=AsyncMock(return_value=json.dumps(tags))):
            res = json.loads(await server.weather_alerts({"wind_gust": 100, "frost_temp": 20}))
        assert res["avisos"] and "tempunit" in res["avisos"][0] and res["umbrales"]["wind_gust"] == 100.0
        assert res["unidades"]["tempunit"] == "°F"

    @pytest.mark.asyncio
    async def test_invalid_threshold_fails_before_calling_the_station(self):
        with patch.object(server, "_get", new=AsyncMock()) as g:
            with pytest.raises(ValueError):
                await server.weather_alerts({"bad": 1})
        g.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_station_error_is_reported(self):
        with patch.object(server, "_get", new=AsyncMock(return_value="ERROR HTTP 503 en /api/tags")):
            with pytest.raises(ValueError, match="condiciones actuales"):
                await server.weather_alerts()

    @pytest.mark.asyncio
    async def test_stopped_station_is_an_alert(self):
        with patch.object(server, "_get", new=AsyncMock(return_value=json.dumps(dict(TAGS, temp="15", tempTL="9", wgust="5",
                                                                                      DataStopped="1")))):
            res = json.loads(await server.weather_alerts())
        assert [a["tipo"] for a in res["alertas"]] == ["estación"]


def day(d, **kw):
    return {"_date": d, "fecha": d.isoformat(), **kw}


def month_rows(year, month, ndays, **kw):
    return [day(D(year, month, i + 1), **kw) for i in range(ndays)]


def history():
    rows = []
    for y in (2023, 2024):
        rows += month_rows(y, 7, 31, temp_media=20.0, temp_max=28.0, temp_min=12.0, lluvia_total=0.0, racha_max=30.0,
                           pres_max=1020, pres_min=1010, horas_sol=10.0, uv_max=7, solar_max=900)
    cur = month_rows(2025, 7, 31, temp_media=24.0, temp_max=33.0, temp_min=-1.0, lluvia_total=0.0, racha_max=30.0,
                     pres_max=1020, pres_min=1010, horas_sol=11.0, uv_max=9, solar_max=950)
    for d in (5, 6, 7):                      # tres días de lluvia seguidos
        cur[d - 1].update(lluvia_total=12.0)
    cur[19]["racha_max"] = 71.0
    return rows + cur


class TestBuildPeriodReport:
    def test_month_report_contents(self):
        md = reporting.build_period_report(history(), D(2025, 7, 1), D(2025, 7, 31), "Informe mensual 2025-07")
        assert md.startswith("# Informe mensual 2025-07\n") and "31 de 31 días con datos" in md
        assert "Media del periodo: 24.0 °C" in md and "Máxima absoluta: 33.0 °C (2025-07-01)" in md
        assert "Días de helada (mín ≤ 0): 31" in md and "31 con máx ≥ 30" in md
        assert "Total: 36.0 mm en 3 días de lluvia" in md and "Día más lluvioso: 12.0 mm (2025-07-05)" in md
        assert "Racha seca más larga: 24 días (2025-07-08 → 2025-07-31)" in md
        assert "Racha lluviosa más larga: 3 días (2025-07-05 → 2025-07-07)" in md
        assert "Racha máxima: 71.0 km/h (2025-07-20)" in md and "Horas de sol: 341.0 h" in md

    def test_comparison_with_normals(self):
        md = reporting.build_period_report(history(), D(2025, 7, 1), D(2025, 7, 31), "T")
        assert "## Frente a lo normal" in md
        assert "Temperatura media: 24.0 °C frente a 20.0 °C normal (+4 °C); percentil 100 entre 3 años." in md
        assert "Lluvia acumulada: 36.0 mm frente a 0.0 mm normal" in md

    def test_no_comparison_without_other_years(self):
        md = reporting.build_period_report(month_rows(2025, 7, 31, **{"temp_media": 20.0, "temp_max": 25.0, "temp_min": 10.0,
                                           "lluvia_total": 0.0, "racha_max": 1.0}), D(2025, 7, 1), D(2025, 7, 31), "T")
        assert "Frente a lo normal" not in md

    def test_missing_days_are_reported(self):
        rows = [r for r in history() if not (r["_date"].year == 2025 and r["_date"].day in (3, 4))]
        md = reporting.build_period_report(rows, D(2025, 7, 1), D(2025, 7, 31), "T")
        assert "29 de 31 días con datos" in md and "Faltan 2 días" in md

    def test_empty_period(self):
        md = reporting.build_period_report(history(), D(2030, 1, 1), D(2030, 1, 31), "Vacío")
        assert "No hay datos" in md and md.startswith("# Vacío")


class TestPeriodReportTool:
    def patch_rows(self, rows):
        return patch.object(server, "_all_dayfile", new=AsyncMock(return_value=rows))

    @pytest.mark.asyncio
    async def test_defaults_to_month_ending_at_the_last_data_day(self):
        rows = [r for r in history() if r["_date"] <= D(2025, 7, 20)]
        with self.patch_rows(rows):
            md = await server.period_report()
        assert md.startswith("# Informe mensual 2025-07 (hasta el 20)") and "20 de 20 días con datos" in md

    @pytest.mark.asyncio
    async def test_complete_month_week_and_year(self):
        with self.patch_rows(history()):
            assert (await server.period_report("month", "2025-07-31")).startswith("# Informe mensual 2025-07\n")
            week = await server.period_report("week", "2025-07-10")
            assert week.startswith("# Informe semanal (2025-07-04 → 2025-07-10)") and "7 de 7 días con datos" in week
            year = await server.period_report("year", "2025-07-31")
            assert year.startswith("# Informe anual 2025 (hasta el 2025-07-31)")

    @pytest.mark.asyncio
    async def test_errors(self):
        with self.patch_rows([]):
            with pytest.raises(ValueError, match="vacío"):
                await server.period_report()
        with self.patch_rows(history()):
            with pytest.raises(ValueError):
                await server.period_report("month", "31/07/2025")
