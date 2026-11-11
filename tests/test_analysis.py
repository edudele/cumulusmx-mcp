"""Pruebas de las tools de análisis sobre el histórico:
count_days, find_streaks, anomaly, data_quality, y de la paralelización de today_all / records_all."""
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

D = datetime.date


def row(d: datetime.date, **kw):
    base = {"_date": d, "fecha": d.isoformat()}
    base.update(kw)
    return base


def days(start: datetime.date, values: list, col="temp_max"):
    """Una fila por día consecutivo; None = sin dato."""
    return [row(start + datetime.timedelta(days=i), **{col: v}) for i, v in enumerate(values)]


def patch_dayfile(rows):
    return patch.object(server, "_all_dayfile", new=AsyncMock(return_value=rows))


# ---------------------------------------------------------------- count_days ----

class TestCountDays:
    def test_count_matching_ignores_missing_values(self):
        rows = days(D(2024, 7, 1), [36.0, 34.0, None, 35.0])
        evaluated, hits = server.count_matching(rows, "temp_max", ">=", 35)
        assert evaluated == 3
        assert [r["fecha"] for r in hits] == ["2024-07-01", "2024-07-04"]

    @pytest.mark.asyncio
    async def test_count_days_by_year_with_alias_and_listing(self):
        rows = days(D(2023, 7, 1), [36, 20]) + days(D(2024, 7, 1), [37, 38, 10])
        with patch_dayfile(rows):
            res = json.loads(await server.count_days("maxtemp", ">=", 35, list_days=True))
        assert res["condicion"] == "temp_max >= 35"
        assert res["dias_evaluados"] == 5
        assert res["dias_que_cumplen"] == 3
        assert res["por_anio"] == {"2023": 1, "2024": 2}
        assert res["fechas"] == ["2023-07-01", "2024-07-01", "2024-07-02"]
        assert res["unidad"] == "°C"

    @pytest.mark.asyncio
    async def test_count_days_respects_date_range(self):
        rows = days(D(2024, 7, 1), [40, 40, 40, 40])
        with patch_dayfile(rows):
            res = json.loads(await server.count_days("temp_max", ">", 30, start="2024-07-02", end="2024-07-03"))
        assert res["dias_que_cumplen"] == 2
        assert res["periodo"] == {"desde": "2024-07-02", "hasta": "2024-07-03"}

    @pytest.mark.asyncio
    async def test_count_days_rejects_non_numeric_and_unknown_columns(self):
        with patch_dayfile([]):
            with pytest.raises(ValueError, match="no es una columna numérica"):
                await server.count_days("hora_temp_max", ">", 1)
            with pytest.raises(ValueError, match="no es una columna numérica"):
                await server.count_days("fecha", ">", 1)
            with pytest.raises(ValueError, match="Columnas desconocidas"):
                await server.count_days("inventada", ">", 1)

    @pytest.mark.asyncio
    async def test_count_days_empty_dayfile(self):
        with patch_dayfile([]):
            res = json.loads(await server.count_days("temp_max", ">", 1))
        assert res["dias_que_cumplen"] == 0 and res["periodo"]["desde"] is None

    @pytest.mark.asyncio
    async def test_count_days_list_is_capped(self):
        rows = days(D(2020, 1, 1), [50] * 400)
        with patch_dayfile(rows):
            res = json.loads(await server.count_days("temp_max", ">", 1, list_days=True))
        assert res["dias_que_cumplen"] == 400
        assert len(res["fechas"]) == server.MAX_LIST_ITEMS and res["lista_truncada"] is True


# ---------------------------------------------------------------- find_streaks ----

class TestStreaks:
    def test_runs_sorted_longest_first(self):
        rows = days(D(2024, 7, 1), [36, 36, 20, 36, 36, 36, 36, 20, 36])
        runs = server.compute_streaks(rows, "temp_max", ">=", 35)
        assert runs == [
            {"dias": 4, "desde": "2024-07-04", "hasta": "2024-07-07"},
            {"dias": 2, "desde": "2024-07-01", "hasta": "2024-07-02"},
        ]  # el día suelto del final no llega a min_length=2

    def test_min_length_one_keeps_single_days(self):
        rows = days(D(2024, 7, 1), [36, 20, 36])
        assert len(server.compute_streaks(rows, "temp_max", ">=", 35, min_length=1)) == 2

    def test_missing_value_breaks_streak(self):
        rows = days(D(2024, 7, 1), [36, None, 36])
        assert server.compute_streaks(rows, "temp_max", ">=", 35, min_length=1) == [
            {"dias": 1, "desde": "2024-07-01", "hasta": "2024-07-01"},
            {"dias": 1, "desde": "2024-07-03", "hasta": "2024-07-03"},
        ]

    def test_date_gap_breaks_streak(self):
        rows = days(D(2024, 7, 1), [36, 36]) + days(D(2024, 7, 5), [36, 36])  # faltan 3-4 julio
        runs = server.compute_streaks(rows, "temp_max", ">=", 35)
        assert [r["dias"] for r in runs] == [2, 2]

    @pytest.mark.asyncio
    async def test_find_streaks_tool_reports_ongoing_streak(self):
        rows = days(D(2024, 7, 1), [0, 0, 5, 0, 0, 0], col="lluvia_total")
        with patch_dayfile(rows):
            res = json.loads(await server.find_streaks("rain", "<", 0.2, top=1))
        assert res["rachas"] == [{"dias": 3, "desde": "2024-07-04", "hasta": "2024-07-06"}]
        assert res["racha_en_curso"] == res["rachas"][0]
        assert res["n_rachas"] == 2

    @pytest.mark.asyncio
    async def test_find_streaks_no_ongoing_streak(self):
        rows = days(D(2024, 7, 1), [36, 36, 10])
        with patch_dayfile(rows):
            res = json.loads(await server.find_streaks("temp_max", ">=", 35))
        assert res["racha_en_curso"] is None


# ---------------------------------------------------------------- anomaly ----

def yearly_rows(year_values: dict[int, float], col="temp_media", month=7, ndays=10):
    rows = []
    for y, v in year_values.items():
        rows += days(D(y, month, 1), [v] * ndays, col=col)
    return rows


class TestAnomaly:
    def test_mean_variable_vs_other_years(self):
        rows = yearly_rows({2022: 20.0, 2023: 22.0, 2024: 24.0, 2025: 26.0})
        res = server.compute_anomaly(rows, "temp_media", D(2025, 7, 1), D(2025, 7, 10), [2022, 2023, 2024])
        assert res["metodo"] == "media"
        assert res["valor"] == 26.0 and res["normal"] == 22.0 and res["diferencia"] == 4.0
        assert res["percentil"] == 100 and res["posicion_entre_anios"] == 1 and res["anios_comparados"] == 4
        assert res["por_anio"] == {"2022": 20.0, "2023": 22.0, "2024": 24.0}
        assert "advertencia" not in res
        assert res["diferencia_pct"] is None

    def test_additive_variable_sums_and_reports_percentage(self):
        rows = yearly_rows({2023: 1.0, 2024: 1.0, 2025: 2.0}, col="lluvia_total")
        res = server.compute_anomaly(rows, "lluvia_total", D(2025, 7, 1), D(2025, 7, 10), [2023, 2024])
        assert res["metodo"] == "suma"
        assert res["valor"] == 20.0 and res["normal"] == 10.0
        assert res["diferencia_pct"] == 100.0
        assert "advertencia" in res  # solo 2 años de referencia

    def test_ongoing_period_compares_elapsed_part_only(self):
        rows = yearly_rows({2023: 10.0, 2024: 10.0, 2025: 10.0, 2022: 10.0}, col="lluvia_total")
        rows = [r for r in rows if not (r["_date"].year == 2025 and r["_date"].day > 5)]  # 2025 llega hasta el 5
        res = server.compute_anomaly(rows, "lluvia_total", D(2025, 7, 1), D(2025, 7, 31), [2022, 2023, 2024])
        assert res["periodo"]["parcial"] is True and res["periodo"]["end"] == "2025-07-05"
        assert res["valor"] == 50.0 and res["normal"] == 50.0 and res["diferencia"] == 0.0

    def test_years_without_enough_coverage_are_skipped(self):
        rows = yearly_rows({2023: 20.0, 2025: 26.0})
        rows += days(D(2024, 7, 1), [99.0, 99.0])  # 2024: solo 2 de 10 días
        res = server.compute_anomaly(rows, "temp_media", D(2025, 7, 1), D(2025, 7, 10), [2023, 2024])
        assert list(res["por_anio"]) == ["2023"]

    def test_errors_when_no_data_or_no_baseline(self):
        rows = yearly_rows({2025: 26.0})
        with pytest.raises(ValueError, match="No hay años de referencia"):
            server.compute_anomaly(rows, "temp_media", D(2025, 7, 1), D(2025, 7, 10), [2024])
        with pytest.raises(ValueError, match="No hay datos"):
            server.compute_anomaly(rows, "temp_media", D(2026, 1, 1), D(2026, 1, 10), [2025])
        with pytest.raises(ValueError, match="Datos insuficientes"):
            server.compute_anomaly(rows, "temp_media", D(2025, 6, 20), D(2025, 7, 10), [2024])

    def test_feb_29_start_maps_to_feb_28_in_non_leap_years(self):
        rows = (days(D(2024, 2, 29), [10.0] * 5, col="temp_media")      # 29 feb - 4 mar 2024
                + days(D(2023, 2, 28), [8.0] * 5, col="temp_media"))     # ventana equivalente: 28 feb - 4 mar 2023
        res = server.compute_anomaly(rows, "temp_media", D(2024, 2, 29), D(2024, 3, 4), [2023])
        assert res["valor"] == 10.0 and res["normal"] == 8.0

    @pytest.mark.asyncio
    async def test_anomaly_tool_excludes_period_year_and_honours_baseline(self):
        rows = yearly_rows({2020: 100.0, 2023: 20.0, 2024: 22.0, 2025: 26.0})
        with patch_dayfile(rows):
            res = json.loads(await server.anomaly("2025-07-01", "2025-07-10", baseline_start_year=2023))
            assert list(res["por_anio"]) == ["2023", "2024"]  # 2020 fuera de la base, 2025 es el periodo
            res_all = json.loads(await server.anomaly("2025-07-01", "2025-07-10", baseline_start_year=2000))
            assert "2020" in res_all["por_anio"]

    @pytest.mark.asyncio
    async def test_anomaly_tool_validates_inputs(self):
        with patch_dayfile([]):
            with pytest.raises(ValueError, match="end anterior"):
                await server.anomaly("2025-07-10", "2025-07-01")
            with pytest.raises(ValueError, match="no es una columna numérica"):
                await server.anomaly("2025-07-01", "2025-07-10", variable="fecha")


# ---------------------------------------------------------------- data_quality ----

class TestDataQuality:
    def test_clean_dataset(self):
        rows = [row(D(2024, 1, 1) + datetime.timedelta(days=i), temp_min=1.0, temp_max=9.0,
                    hum_min=40, hum_max=90, pres_min=1000, pres_max=1010, lluvia_total=0.0, horas_sol=3.0)
                for i in range(5)]
        res = server.check_quality(rows)
        assert res["ok"] is True and res["dias_esperados"] == 5 and res["dias_faltantes"]["total"] == 0

    def test_detects_gaps_and_inconsistencies(self):
        rows = [
            row(D(2024, 1, 1), temp_min=1.0, temp_max=9.0),
            row(D(2024, 1, 4), temp_min=12.0, temp_max=9.0),                       # min > max
            row(D(2024, 1, 5), temp_min=None, temp_max=None),                      # sin temperatura
            row(D(2024, 1, 6), temp_min=1.0, temp_max=2.0, hum_min=-5, hum_max=120),
            row(D(2024, 1, 7), temp_min=1.0, temp_max=2.0, hum_min=80, hum_max=70, pres_min=1020, pres_max=1000,
                lluvia_total=-1.0, horas_sol=-2.0),
        ]
        res = server.check_quality(rows)
        assert res["ok"] is False
        assert res["dias_faltantes"] == {"total": 2, "fechas": ["2024-01-02", "2024-01-03"]}
        assert res["dias_sin_temperatura"]["fechas"] == ["2024-01-05"]
        problems = " | ".join(i["problema"] for i in res["valores_incoherentes"]["detalle"])
        for expected in ("temp_min (12.0) > temp_max", "hum_min fuera de 0-100", "hum_max fuera de 0-100",
                         "hum_min (80.0) > hum_max", "pres_min (1020.0) > pres_max", "lluvia_total negativo",
                         "horas_sol negativo"):
            assert expected in problems

    def test_lists_are_capped_but_totals_are_not(self):
        rows = [row(D(2020, 1, 1), temp_min=1.0, temp_max=2.0), row(D(2021, 1, 1), temp_min=1.0, temp_max=2.0)]
        res = server.check_quality(rows, max_items=10)
        assert res["dias_faltantes"]["total"] > 300 and len(res["dias_faltantes"]["fechas"]) == 10

    def test_empty(self):
        assert server.check_quality([]) == {"n_dias": 0}

    @pytest.mark.asyncio
    async def test_tool_filters_range(self):
        rows = days(D(2024, 1, 1), [5.0] * 10, col="temp_max")
        with patch_dayfile(rows):
            res = json.loads(await server.data_quality(start="2024-01-03", end="2024-01-05"))
        assert res["n_dias"] == 3 and res["desde"] == "2024-01-03"


# ---------------------------------------------------------------- paralelización ----

class TestParallelAggregators:
    @pytest.mark.asyncio
    async def test_today_all_keeps_all_keys_in_order(self):
        with patch.object(server, "_get", new=AsyncMock(side_effect=lambda p, *a, **k: json.dumps({"p": p}))):
            res = json.loads(await server.today_all(strip_html=False))
        assert list(res) == ["temp", "hum", "rain", "wind", "pressure", "solar"]
        assert res["rain"] == {"p": "/api/todayyest/rain.json"}

    @pytest.mark.asyncio
    async def test_records_all_month_and_validation(self):
        mock = AsyncMock(side_effect=lambda p, *a, **k: "not-json" if "humidity" in p else json.dumps({"p": p}))
        with patch.object(server, "_get", new=mock):
            res = json.loads(await server.records_all("month", month=7, strip_html=False))
        assert list(res) == ["temperature", "humidity", "pressure", "wind", "rain"]
        assert res["humidity"] == "not-json"
        assert res["wind"] == {"p": "/api/records/month/7/wind.json"}
        assert mock.await_count == 5
        with pytest.raises(ValueError, match="month debe estar entre 1 y 12"):
            await server.records_all("month", month=13)


# ---------------------------------------------------------------- vía protocolo MCP ----

class TestThroughMcpClient:
    """Llama a las tools como lo hace un agente: con validación de esquema y serialización MCP."""

    @pytest.mark.asyncio
    async def test_new_tools_work_through_mcp_protocol(self):
        from fastmcp import Client

        rows = yearly_rows({2023: 20.0, 2024: 22.0, 2025: 26.0}, col="temp_max")
        with patch_dayfile(rows):
            async with Client(server.mcp) as client:
                res = await client.call_tool("count_days", {"variable": "temp_max", "op": ">=", "value": 25})
                assert json.loads(res.content[0].text)["dias_que_cumplen"] == 10

                res = await client.call_tool("find_streaks", {"variable": "temp_max", "op": ">", "value": 21, "top": 1})
                assert json.loads(res.content[0].text)["rachas"][0]["dias"] == 10

                res = await client.call_tool("anomaly", {"start": "2025-07-01", "end": "2025-07-10",
                                                         "variable": "temp_max", "baseline_start_year": 2000})
                assert json.loads(res.content[0].text)["normal"] == 21.0

                res = await client.call_tool("data_quality", {})
                assert json.loads(res.content[0].text)["n_dias"] == 30

    @pytest.mark.asyncio
    async def test_invalid_operator_is_rejected_by_schema(self):
        from fastmcp import Client
        from fastmcp.exceptions import ToolError

        with patch_dayfile([]):
            async with Client(server.mcp) as client:
                with pytest.raises(ToolError):
                    await client.call_tool("count_days", {"variable": "temp_max", "op": "!=", "value": 1})
