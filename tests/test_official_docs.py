"""Pruebas basadas en la documentación oficial de la API de CumulusMX (wiki + código fuente 5.2.0) y en el
comportamiento observado en una estación real:

- `rc` (tags con punto decimal), unidades de las series de gráficas (`units.json` plano),
- orden de columnas del dayfile (fila de ejemplo de la wiki), centinelas -9999/9999,
- tope de fechas empatadas, humedad 0 %, columnas añadidas (24 h, BGT, WBGT).
"""
import datetime
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-secret-token-12345")

import server  # noqa: E402

# Fila de ejemplo tomada literalmente de la wiki "API Data Logs" (línea 3511 del dayfile, formato antiguo de 53 campos)
WIKI_DAYFILE_ROW = [
    3511, "24/08/19", "15.0", "142", "15:04", "12.1", "06:03", "27.9", "16:25", "1015.58", "18:47", "1019.95",
    "00:31", "0.0", "00:00", "0.0", "20.3", "61.3", "7.0", "12:14", "44", "16:13", "98", "07:13", "2.77", "12.0",
    "28.1", "16:29", "28.3", "16:38", "12.2", "06:12", "0.0", "00:00", "12.1", "06:03", "17.1", "13:31", "11.3",
    "06:03", "158", "0.5", "3.4", "417", "12:50", "2.1", "14:13", "", "", "", "", "", "",
]


# ---------------------------------------------------------------- tags con `rc` ----

class TestPlainNumbersTags:
    def test_rc_must_be_first_query_parameter(self):
        assert server._tags_path(["temp", "hum"]) == "/api/tags/process.json?rc&temp&hum"
        assert server._tags_path(["temp"], plain_numbers=False) == "/api/tags/process.json?temp"

    @pytest.mark.asyncio
    async def test_get_tags_uses_rc_by_default_and_can_opt_out(self):
        mock = AsyncMock(return_value='{"temp":"10.0"}')
        with patch.object(server, "_get", new=mock):
            await server.get_tags(["temp", "press"])
            mock.assert_awaited_with("/api/tags/process.json?rc&temp&press")
            await server.get_tags(["temp"], plain_numbers=False)
            mock.assert_awaited_with("/api/tags/process.json?temp")

    @pytest.mark.asyncio
    async def test_get_tags_still_validates_names(self):
        with pytest.raises(ValueError):
            await server.get_tags(["temp&rc"])
        with pytest.raises(ValueError):
            await server.get_tags([])

    @pytest.mark.asyncio
    async def test_snapshot_uses_rc(self):
        mock = AsyncMock(return_value="{}")
        with patch.object(server, "_get", new=mock):
            await server.snapshot()
        path = mock.await_args.args[0]
        assert path.startswith("/api/tags/process.json?rc&temp&")


# ---------------------------------------------------------------- unidades en graph_summary ----

GRAPH = {
    "temp": [[1704067200000, 10.5], [1704070800000, 12.0]],
    "wspeed": [[1704067200000, 3.0], [1704070800000, 5.0]],
    "rrate": [[1704067200000, 0.0], [1704070800000, 1.2]],
    "hum": [[1704067200000, 80], [1704070800000, 70]],
    "press": [[1704067200000, 1010.0], [1704070800000, 1011.0]],
    "bearing": [[1704067200000, 90], [1704070800000, 120]],
    "desconocida": [[1704067200000, 1], [1704070800000, 2]],
}
# Formato real de units.json en CumulusMX 5.2.0 (observado en una estación)
UNITS_5_2 = {"temp": "C", "wind": "km/h", "windrun": "km", "rain": "mm", "press": "hPa",
             "soilmoisture": ["", ""], "co2": "ppm", "leafwet": "%", "aq": "µg/m³", "snow": "cm", "laser": "cm"}


def json_by_path(units):
    async def fake(path, params=None):
        if path.endswith("units.json"):
            if isinstance(units, Exception):
                raise units
            return units
        return GRAPH
    return fake


class TestGraphSummaryUnits:
    @pytest.mark.asyncio
    async def test_units_added_per_series(self):
        with patch.object(server, "_json", new=json_by_path(UNITS_5_2)):
            res = json.loads(await server.graph_summary("tempdata"))
        assert res["temp"]["unidad"] == "°C"
        assert res["wspeed"]["unidad"] == "km/h"
        assert res["rrate"]["unidad"] == "mm/h"
        assert res["press"]["unidad"] == "hPa"
        assert res["hum"]["unidad"] == "%"
        assert res["bearing"]["unidad"] == "°"
        assert "unidad" not in res["desconocida"]

    @pytest.mark.asyncio
    async def test_fahrenheit_and_legacy_wiki_format(self):
        legacy = {"temp": {"units": "F", "decimals": 1}, "wind": {"units": "mph", "decimals": 1}}
        with patch.object(server, "_json", new=json_by_path(legacy)):
            res = json.loads(await server.graph_summary("tempdata"))
        assert res["temp"]["unidad"] == "°F" and res["wspeed"]["unidad"] == "mph"

    @pytest.mark.asyncio
    async def test_summary_still_works_when_units_unavailable(self):
        with patch.object(server, "_json", new=json_by_path(ValueError("HTTP 500"))):
            res = json.loads(await server.graph_summary("tempdata"))
        assert res["temp"]["max"] == 12.0 and "unidad" not in res["temp"]
        # Respuesta con formato inesperado (lista en vez de dict)
        with patch.object(server, "_json", new=json_by_path(["x"])):
            res = json.loads(await server.graph_summary("tempdata"))
        assert "unidad" not in res["temp"]

    def test_series_unit_helper(self):
        assert server._series_unit("dailyrain", {"rain": "in"}) == "in"
        assert server._series_unit("SolarRad", {}) == "W/m²"
        assert server._series_unit("temp", {}) is None


# ---------------------------------------------------------------- dayfile: columnas y valores ----

class TestDayfileColumns:
    def test_column_order_matches_official_field_names(self):
        """Posiciones tomadas de DayfileFieldNames (CumulusMX 5.2.0); la columna 0 de la API es el nº de línea."""
        official_index = {
            "Date": 0, "HighWindGust": 1, "MinTemperature": 4, "MaxTemperature": 6, "MinPressure": 8,
            "TotalRainfallToday": 14, "AvgTemperatureToday": 15, "LowHumidity": 19,
            "TotalHoursOfSunshine": 24, "HighUv-I": 44, "HighHumidex": 50, "ChillHours": 52,
            "High24hRain": 53, "HighBgt": 55, "HighWbgt": 57, "HighWbgtTime": 58,
        }
        ours = {
            "Date": "fecha", "HighWindGust": "racha_max", "MinTemperature": "temp_min", "MaxTemperature": "temp_max",
            "MinPressure": "pres_min", "TotalRainfallToday": "lluvia_total", "AvgTemperatureToday": "temp_media",
            "LowHumidity": "hum_min", "TotalHoursOfSunshine": "horas_sol", "HighUv-I": "uv_max",
            "HighHumidex": "humidex_max", "ChillHours": "horas_frio", "High24hRain": "lluvia_24h_max",
            "HighBgt": "bgt_max", "HighWbgt": "wbgt_max", "HighWbgtTime": "hora_wbgt_max",
        }
        for off, idx in official_index.items():
            assert server.DAYFILE_COLS[idx + 1] == ours[off], off
        assert len(server.DAYFILE_COLS) == 1 + 59

    def test_new_columns_resolve_with_aliases(self):
        assert server.resolve_columns(["wbgt", "BGT", "rain24h"]) == ["fecha", "wbgt_max", "bgt_max", "lluvia_24h_max"]
        assert server.UNITS["lluvia_24h_max"] == "mm"

    @pytest.mark.asyncio
    async def test_wiki_example_row_is_parsed_correctly(self):
        fake = {"data": [WIKI_DAYFILE_ROW], "recordsTotal": 1}
        with patch.object(server, "_json", new=AsyncMock(return_value=fake)):
            rows = await server._fetch_all_dayfile_raw()
        r = rows[0]
        assert r["_date"] == datetime.date(2019, 8, 24) and r["fecha"] == "2019-08-24"
        assert (r["racha_max"], r["temp_min"], r["temp_max"], r["pres_min"]) == ("15.0", "12.1", "27.9", "1015.58")
        assert (r["hum_min"], r["ET"], r["horas_sol"], r["dir_viento_dominante"]) == ("44", "2.77", "12.0", "158")
        assert (r["solar_max"], r["uv_max"], r["hora_uv_max"]) == ("417", "2.1", "14:13")
        # Campos vacíos de versiones antiguas -> sin dato, no error
        assert server._num(r.get("feels_max")) is None and r.get("wbgt_max") is None

    def test_num_treats_cumulus_sentinels_as_missing(self):
        for missing in ("9999", 9999, -9999, "-9999", -999.0, "", "-", None, float("nan")):
            assert server._num(missing) is None, missing
        for valid in ("0", 0, -50.2, "1013.2", 1613, "5,0".replace(",", ".")):
            assert server._num(valid) is not None, valid

    def test_sentinel_low_value_does_not_pollute_rankings(self):
        rows = [{"fecha": f"2024-01-0{i}", "temp_min": v} for i, v in enumerate([5.0, 9999.0, -3.0], start=1)]
        top = server.rank(rows, "temp_min", top=3, order="desc")
        assert [t["valor"] for t in top] == [5.0, -3.0]


class TestTieDatesCap:
    def test_ties_are_capped_and_total_reported(self):
        rows = [{"fecha": f"2024-01-{d:02d}", "lluvia_rate_max": 0.0} for d in range(1, 29)]
        rows += [{"fecha": "2024-02-01", "lluvia_rate_max": 5.0}]
        ext = server.extreme(rows, "lluvia_rate_max", "min")
        assert ext["valor"] == 0.0 and ext["fecha"] == "2024-01-01"
        assert len(ext["fechas"]) == server.MAX_TIE_DATES and ext["n_fechas"] == 28

    def test_few_ties_unchanged(self):
        rows = [{"fecha": "2024-03-05", "t": -4.0}, {"fecha": "2024-01-10", "t": -4.0}]
        ext = server.extreme(rows, "t", "min")
        assert ext["fechas"] == ["2024-01-10", "2024-03-05"] and "n_fechas" not in ext

    def test_summary_payload_stays_small_with_hundreds_of_ties(self):
        rows = [{"fecha": "2024-01-01", "lluvia_rate_max": 0.0, "_date": datetime.date(2024, 1, 1)}] * 600
        out = json.dumps(server.summarize(rows, ["fecha", "lluvia_rate_max"]))
        assert len(out) < 1200


class TestDataQualityHumidityZero:
    def test_zero_humidity_is_flagged_as_probable_sensor_fault(self):
        rows = [{"_date": datetime.date(2025, 6, 11), "fecha": "2025-06-11", "temp_min": 8.0, "temp_max": 20.0,
                 "hum_min": 0.0, "hum_max": 70}]
        res = server.check_quality(rows)
        assert res["ok"] is False
        assert "hum_min = 0 % (posible fallo del sensor)" in res["valores_incoherentes"]["detalle"][0]["problema"]
