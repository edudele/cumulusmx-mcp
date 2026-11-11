"""Pruebas de las tools de análisis avanzado (climatology, frost_dates, degree_days, wind_rose, daily_briefing)
y de la base de fiabilidad (reintentos), el modo solo lectura real y la guía para agentes."""
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-secret-token-12345")

import server  # noqa: E402

D = datetime.date


def row(d, **kw):
    return {"_date": d, "fecha": d.isoformat(), **kw}


def month_rows(year, month, ndays=None, **cols):
    n = ndays if ndays is not None else __import__("calendar").monthrange(year, month)[1]
    return [row(D(year, month, i + 1), **cols) for i in range(n)]


def patch_dayfile(rows):
    return patch.object(server, "_all_dayfile", new=AsyncMock(return_value=rows))


# ---------------------------------------------------------------- climatology ----

class TestClimatology:
    def test_monthly_means_extremes_and_record_years(self):
        rows = (month_rows(2023, 7, temp_media=20.0, temp_max=30.0, temp_min=10.0, lluvia_total=0.0, horas_sol=10.0, racha_max=40.0)
                + month_rows(2024, 7, temp_media=24.0, temp_max=36.0, temp_min=12.0, lluvia_total=1.0, horas_sol=12.0, racha_max=50.0))
        july = server.compute_climatology(rows)["07"]
        assert july["n_anios"] == 2
        assert july["temp_media"] == 22.0 and july["temp_max_media"] == 33.0 and july["temp_min_media"] == 11.0
        assert july["lluvia_total_media"] == 15.5  # (0*31 + 1*31) / 2
        assert july["dias_lluvia_media"] == 15.5   # 2024: 31 días con 1 mm >= 0,2; 2023: 0
        assert july["horas_sol_media"] == 341.0
        assert july["temp_max_absoluta"] == {"anio": 2024, "valor": 36.0}
        assert july["temp_min_absoluta"] == {"anio": 2023, "valor": 10.0}
        assert july["mes_mas_calido"] == {"anio": 2024, "valor": 24.0}
        assert july["mes_mas_frio"] == {"anio": 2023, "valor": 20.0}
        assert july["mes_mas_lluvioso"]["anio"] == 2024 and july["mes_mas_seco"]["anio"] == 2023
        assert july["racha_max"] == {"anio": 2024, "valor": 50.0}

    def test_incomplete_months_are_ignored(self):
        rows = month_rows(2023, 7, temp_media=20.0) + month_rows(2024, 7, ndays=10, temp_media=99.0)
        july = server.compute_climatology(rows)["07"]
        assert july["n_anios"] == 1 and july["temp_media"] == 20.0

    def test_leap_february_needs_29_days_worth_of_coverage(self):
        rows = month_rows(2024, 2, ndays=22, temp_media=5.0)  # 22/29 = 76 % < 80 %
        assert "02" not in server.compute_climatology(rows)
        rows = month_rows(2024, 2, ndays=24, temp_media=5.0)  # 24/29 = 83 %
        assert server.compute_climatology(rows)["02"]["temp_media"] == 5.0

    def test_months_without_data_are_omitted(self):
        out = server.compute_climatology(month_rows(2024, 3, temp_media=8.0))
        assert list(out) == ["03"]

    @pytest.mark.asyncio
    async def test_tool_filters_years_and_validates(self):
        rows = month_rows(2022, 7, temp_media=1.0) + month_rows(2023, 7, temp_media=3.0) + month_rows(2024, 7, temp_media=5.0)
        with patch_dayfile(rows):
            res = json.loads(await server.climatology(start_year=2023, end_year=2024))
            assert res["anios_con_datos"] == [2023, 2024] and res["meses"]["07"]["temp_media"] == 4.0
            with pytest.raises(ValueError, match="posterior"):
                await server.climatology(start_year=2025, end_year=2024)
            with pytest.raises(ValueError, match="No hay datos"):
                await server.climatology(start_year=2030, end_year=2031)


# ---------------------------------------------------------------- frost_dates ----

def frost_year(year, frosts: dict[str, float], base=5.0):
    """Año completo con temp_min = base salvo las fechas indicadas (ISO -> valor)."""
    rows = []
    d = D(year, 1, 1)
    while d.year == year:
        rows.append(row(d, temp_min=frosts.get(d.isoformat(), base)))
        d += datetime.timedelta(days=1)
    return rows


class TestFrostDates:
    def test_north_hemisphere_last_spring_first_autumn_and_free_period(self):
        rows = frost_year(2024, {"2024-01-10": -3, "2024-04-20": -1, "2024-05-02": -0.5, "2024-10-15": -2,
                                 "2024-12-01": -4, "2024-03-01": 1.0})
        r = server.compute_frost_dates(rows, 2024, 0.0)
        assert r["ultima_helada_primavera"] == "2024-05-02"
        assert r["primera_helada_otono"] == "2024-10-15"
        assert r["dias_helada"] == 5
        assert r["periodo_sin_heladas_dias"] == (D(2024, 10, 15) - D(2024, 5, 2)).days - 1
        assert r["parcial"] is False

    def test_threshold_is_inclusive_and_configurable(self):
        rows = frost_year(2024, {"2024-04-01": 0.0})
        assert server.compute_frost_dates(rows, 2024, 0.0)["ultima_helada_primavera"] == "2024-04-01"
        assert server.compute_frost_dates(rows, 2024, -1.0)["ultima_helada_primavera"] is None
        assert server.compute_frost_dates(rows, 2024, 6.0)["dias_helada"] == 366  # todos los días <= 6

    def test_southern_hemisphere_uses_first_and_last_frost_of_the_year(self):
        rows = frost_year(2024, {"2024-05-10": -1, "2024-07-01": -3, "2024-09-20": -0.5})
        r = server.compute_frost_dates(rows, 2024, 0.0, hemisphere="south")
        assert r["primera_helada_otono"] == "2024-05-10" and r["ultima_helada_primavera"] == "2024-09-20"
        assert r["periodo_sin_heladas_dias"] is None

    def test_no_frost_year(self):
        r = server.compute_frost_dates(frost_year(2024, {}), 2024, 0.0)
        assert r["dias_helada"] == 0 and r["ultima_helada_primavera"] is None and r["periodo_sin_heladas_dias"] is None

    def test_partial_years_are_flagged(self):
        short = [row(D(2024, 1, 1) + datetime.timedelta(days=i), temp_min=5.0) for i in range(100)]
        assert server.compute_frost_dates(short, 2024, 0.0)["parcial"] is True
        full = frost_year(2024, {})
        assert server.compute_frost_dates(full, 2024, 0.0, today=D(2024, 6, 1))["parcial"] is True  # año en curso
        assert server.compute_frost_dates(full, 2024, 0.0, today=D(2025, 6, 1))["parcial"] is False

    def test_mean_month_day(self):
        assert server._mean_month_day(["2023-04-20", "2024-04-30"]) in ("04-25", "04-26")
        assert server._mean_month_day([]) is None

    @pytest.mark.asyncio
    async def test_tool_averages_only_complete_years(self):
        rows = (frost_year(2022, {"2022-04-10": -1, "2022-10-10": -1})
                + frost_year(2023, {"2023-04-20": -1, "2023-10-20": -1})
                + [row(D(2024, 1, 1) + datetime.timedelta(days=i), temp_min=-1.0) for i in range(30)])  # parcial
        with patch_dayfile(rows), patch.object(server, "_today", return_value=D(2024, 3, 1)):
            res = json.loads(await server.frost_dates(start_year=2022, end_year=2024))
        assert [y["anio"] for y in res["anios"]] == [2022, 2023, 2024]
        assert res["anios"][2]["parcial"] is True
        assert res["fecha_media"]["anios_completos"] == 2
        assert res["fecha_media"]["ultima_helada_primavera"] == "04-15"
        assert res["fecha_media"]["primera_helada_otono"] == "10-15"
        assert res["umbral"] == server.FROST_THRESHOLD


# ---------------------------------------------------------------- degree_days ----

class TestDegreeDays:
    def test_average_method_floors_at_zero(self):
        rows = [row(D(2024, 5, 1), temp_min=8.0, temp_max=20.0), row(D(2024, 5, 2), temp_min=0.0, temp_max=8.0),
                row(D(2024, 5, 3), temp_min=None, temp_max=25.0)]
        out = dict(server.compute_degree_days(rows, base=10))
        assert out[D(2024, 5, 1)] == 4.0   # (20+8)/2 - 10
        assert out[D(2024, 5, 2)] == 0.0   # media 4 < base
        assert D(2024, 5, 3) not in out    # sin tmin

    def test_modified_method_applies_floor_and_ceiling(self):
        rows = [row(D(2024, 7, 1), temp_min=5.0, temp_max=40.0)]
        # tmin -> 10, tmax -> 30 : (30+10)/2 - 10 = 10
        assert dict(server.compute_degree_days(rows, base=10, upper=30, method="modified"))[D(2024, 7, 1)] == 10.0
        # sin techo: (40+10)/2 - 10 = 15
        assert dict(server.compute_degree_days(rows, base=10, method="modified"))[D(2024, 7, 1)] == 15.0
        # tmax por debajo de la base -> 0
        cold = [row(D(2024, 1, 1), temp_min=-5.0, temp_max=4.0)]
        assert dict(server.compute_degree_days(cold, base=10, upper=30, method="modified"))[D(2024, 1, 1)] == 0.0

    @pytest.mark.asyncio
    async def test_tool_totals_by_month_and_year_and_defaults(self):
        rows = [row(D(2024, 4, 30), temp_min=10.0, temp_max=20.0), row(D(2024, 5, 1), temp_min=10.0, temp_max=30.0),
                row(D(2024, 5, 2), temp_min=10.0, temp_max=30.0), row(D(2023, 5, 1), temp_min=10.0, temp_max=50.0)]
        with patch_dayfile(rows), patch.object(server, "_today", return_value=D(2024, 6, 1)):
            res = json.loads(await server.degree_days(base=10))       # por defecto: desde 1-ene del año actual
            assert res["periodo"] == {"desde": "2024-04-30", "hasta": "2024-05-02"}
            assert res["por_mes"] == {"2024-04": 5.0, "2024-05": 20.0} and res["total"] == 25.0
            assert res["por_anio"] == {"2024": 25.0}
            res = json.loads(await server.degree_days(start="2023-01-01", end="2023-12-31", base=10))
            assert res["total"] == 20.0
            with pytest.raises(ValueError, match="mayor que base"):
                await server.degree_days(base=10, upper=10)


# ---------------------------------------------------------------- wind_rose ----

class TestWindRose:
    @pytest.mark.parametrize("deg,sector", [(0, "N"), (11.24, "N"), (11.25, "NNE"), (90, "E"), (180, "S"),
                                            (270, "W"), (348.75, "N"), (350, "N"), (359.9, "N"), (360, "N"), (-90, "W")])
    def test_compass_sector_boundaries(self, deg, sector):
        assert server.compass_sector(deg) == sector

    def test_rose_counts_percentages_and_gusts(self):
        rows = [row(D(2024, 1, 1), dir_viento_dominante=90, dir_racha=100, racha_max=40.0),
                row(D(2024, 1, 2), dir_viento_dominante=95, dir_racha=95, racha_max=60.0),
                row(D(2024, 1, 3), dir_viento_dominante=270, dir_racha=None, racha_max=30.0),
                row(D(2024, 1, 4), dir_viento_dominante=None, dir_racha=270, racha_max=None)]
        rose = server.compute_wind_rose(rows)
        by = {s["sector"]: s for s in rose["sectores"]}
        assert rose["dias_con_direccion"] == 3 and rose["sector_mas_frecuente"] == "E"
        assert by["E"] == {"sector": "E", "dias": 2, "pct": 66.7} and by["W"]["dias"] == 1 and by["N"]["pct"] == 0.0
        assert len(rose["sectores"]) == 16
        assert rose["rachas_por_sector"] == [{"sector": "E", "dias_con_racha_maxima_en_este_sector": 2, "racha_max": 60.0}]

    def test_empty(self):
        rose = server.compute_wind_rose([])
        assert rose["dias_con_direccion"] == 0 and rose["sector_mas_frecuente"] is None

    @pytest.mark.asyncio
    async def test_tool_range(self):
        rows = [row(D(2024, 1, 1), dir_viento_dominante=0), row(D(2024, 2, 1), dir_viento_dominante=180)]
        with patch_dayfile(rows):
            res = json.loads(await server.wind_rose(start="2024-02-01"))
        assert res["sector_mas_frecuente"] == "S" and res["periodo"]["desde"] == "2024-02-01"


# ---------------------------------------------------------------- daily_briefing ----

class TestDailyBriefing:
    def history(self):
        rows = []
        for y in (2022, 2023, 2024, 2025):          # mismos meses de otros años
            rows += month_rows(y, 11, temp_media=8.0, temp_max=14.0, temp_min=2.0, lluvia_total=1.0)
        # noviembre 2026 hasta el día 10: 4 últimos días secos, helada el 9 y 10
        for d in range(1, 11):
            rows.append(row(D(2026, 11, d), temp_media=12.0, temp_max=16.0 if d == 10 else 15.0,
                            temp_min=-1.0 if d >= 9 else 3.0,
                            lluvia_total=0.0 if d >= 7 else 5.0))
        return rows

    @pytest.mark.asyncio
    async def test_briefing_combines_live_and_history(self):
        tags = {"temp": "10.5", "hum": "80", "time": "12:30", "tempunit": "&#176;C", "date": "11/11/26"}
        with patch.object(server, "_get", new=AsyncMock(return_value=json.dumps(tags))) as g, \
                patch_dayfile(self.history()), patch.object(server, "_today", return_value=D(2026, 11, 11)):
            res = json.loads(await server.daily_briefing())
        assert g.await_args.args[0].startswith("/api/tags/process.json?rc&temp&")
        assert res["ahora"]["temp"] == 10.5 and res["ahora"]["hum"] == 80.0 and res["ahora"]["tempunit"] == "°C"
        assert res["ahora"]["time"] == "12:30"
        assert res["datos_hasta"] == "2026-11-10" and "aviso_historico" not in res
        ctx = res["ultimo_dia_cerrado_en_contexto"]
        assert ctx["fecha"] == "2026-11-10"
        assert ctx["temp_max"]["posicion"] == 1 and ctx["temp_max"]["percentil"] == 100   # 16 > todos los demás
        assert ctx["temp_max"]["de"] == 130                                               # 120 + 9 + el propio día
        assert ctx["lluvia_total"]["percentil"] == 0                                      # 0 mm < 1 mm
        month = res["mes_en_curso"]
        assert month["temp_media"]["valor"] == 12.0 and month["temp_media"]["normal"] == 8.0
        assert month["temp_media"]["diferencia"] == 4.0
        assert month["lluvia_total"]["valor"] == 30.0   # 6 días * 5 mm
        assert res["rachas_en_curso"] == {"dias_sin_lluvia": 4, "dias_con_tmax_ge_30": 0, "dias_de_helada": 2}

    @pytest.mark.asyncio
    async def test_stale_history_is_reported_and_streaks_withheld(self):
        with patch.object(server, "_get", new=AsyncMock(return_value="{}")), patch_dayfile(self.history()), \
                patch.object(server, "_today", return_value=D(2026, 11, 25)):
            res = json.loads(await server.daily_briefing())
        assert "desactualizado" in res["aviso_historico"] and res["rachas_en_curso"] is None

    @pytest.mark.asyncio
    async def test_degrades_when_history_unavailable_or_tags_invalid(self):
        failing = AsyncMock(side_effect=ValueError("CumulusMX caído"))
        with patch.object(server, "_get", new=AsyncMock(return_value="ERROR HTTP 503 en /api/tags")), \
                patch.object(server, "_all_dayfile", new=failing):
            res = json.loads(await server.daily_briefing())
        assert "CumulusMX caído" in res["aviso_historico"] and "ERROR HTTP 503" in res["aviso_ahora"]
        assert res["ahora"] == {}

    @pytest.mark.asyncio
    async def test_first_day_of_month_has_no_month_anomaly_but_still_works(self):
        rows = self.history()
        with patch.object(server, "_get", new=AsyncMock(return_value="{}")), patch_dayfile(rows), \
                patch.object(server, "_today", return_value=D(2026, 12, 1)):
            res = json.loads(await server.daily_briefing())
        assert "No hay datos" in res["mes_en_curso"]["temp_media"]["aviso"]

    def test_percentile_in_history(self):
        rows = [row(D(2020 + i, 7, 1), t=float(i)) for i in range(10)]           # 0..9
        res = server.percentile_in_history(rows, "t", 4.5, 7, D(2030, 7, 1))
        assert res == {"valor": 4.5, "percentil": 50, "posicion": 6, "de": 11}
        assert server.percentile_in_history(rows[:3], "t", 1.0, 7, D(2030, 7, 1)) is None   # pocas muestras
        # el propio día se excluye
        assert server.percentile_in_history(rows, "t", 9.0, 7, D(2029, 7, 1))["de"] == 10


# ---------------------------------------------------------------- reintentos ----

def resp(status, text="ok"):
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.text = text
    r.json.return_value = {}
    r.raise_for_status.side_effect = None if status < 400 else httpx.HTTPStatusError(
        "err", request=MagicMock(), response=MagicMock(status_code=status, text=text))
    return r


class TestRetries:
    @pytest.mark.asyncio
    async def test_transient_connection_errors_are_retried_then_succeed(self):
        api = AsyncMock(side_effect=[httpx.ConnectError("x"), httpx.ReadTimeout("y"), resp(200, "datos")])
        with patch.object(server, "_api_request", new=api), patch.object(server.asyncio, "sleep", new=AsyncMock()) as sl:
            assert await server._get("/api/x") == "datos"
        assert api.await_count == 3 and sl.await_count == 2
        assert [c.args[0] for c in sl.await_args_list] == [0.3, 0.6]    # backoff exponencial

    @pytest.mark.asyncio
    async def test_gateway_errors_are_retried(self):
        api = AsyncMock(side_effect=[resp(502), resp(504), resp(200, "ok")])
        with patch.object(server, "_api_request", new=api), patch.object(server.asyncio, "sleep", new=AsyncMock()):
            assert await server._get("/api/x") == "ok"
        assert api.await_count == 3

    @pytest.mark.asyncio
    async def test_client_errors_and_503_are_not_retried(self):
        for status in (404, 401, 503):
            api = AsyncMock(return_value=resp(status, "nope"))
            with patch.object(server, "_api_request", new=api), patch.object(server.asyncio, "sleep", new=AsyncMock()):
                out = await server._get("/api/x")
            assert api.await_count == 1 and out.startswith(f"ERROR HTTP {status}")

    @pytest.mark.asyncio
    async def test_gives_up_after_configured_retries(self):
        api = AsyncMock(side_effect=httpx.ConnectError("down"))
        with patch.object(server, "_api_request", new=api), patch.object(server.asyncio, "sleep", new=AsyncMock()), \
                patch.object(server, "CUMULUS_RETRIES", 2):
            out = await server._get("/api/x")
        assert api.await_count == 3 and out.startswith("ERROR de conexión con CumulusMX (ConnectError)")

    @pytest.mark.asyncio
    async def test_zero_retries_means_single_attempt(self):
        api = AsyncMock(side_effect=httpx.ConnectError("down"))
        with patch.object(server, "_api_request", new=api), patch.object(server, "CUMULUS_RETRIES", 0):
            await server._get("/api/x")
        assert api.await_count == 1

    @pytest.mark.asyncio
    async def test_json_helper_also_retries_and_uses_configured_timeout(self):
        ok = resp(200)
        ok.json.return_value = {"a": 1}
        api = AsyncMock(side_effect=[httpx.ConnectError("x"), ok])
        with patch.object(server, "_api_request", new=api), patch.object(server.asyncio, "sleep", new=AsyncMock()), \
                patch.object(server, "CUMULUS_TIMEOUT", 7.5):
            assert await server._json("/api/x") == {"a": 1}
        assert api.await_args.kwargs["timeout"] == 7.5

    @pytest.mark.asyncio
    async def test_post_is_never_retried(self):
        api = AsyncMock(side_effect=httpx.ConnectError("down"))
        with patch.object(server, "_api_request", new=api), patch.object(server.asyncio, "sleep", new=AsyncMock()):
            out = await server._post("/api/utils/ftpnow.json", content="x")
        assert api.await_count == 1 and out.startswith("ERROR de conexión")


# ---------------------------------------------------------------- modo solo lectura real ----

def run_isolated(code: str, **env) -> str:
    full = {**os.environ, "MCP_AUTH_TOKEN": "x", "PYTHONPATH": str(PROJECT_DIR), **env}
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=full, cwd=PROJECT_DIR,
                         timeout=60)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip().splitlines()[-1]


LIST_CODE = (
    "import asyncio, json, server\n"
    "t = asyncio.run(server.mcp.list_tools())\n"
    "ro = [x.name for x in t if x.annotations and x.annotations.read_only_hint]\n"
    "print(json.dumps({'total': len(t), 'ro': len(ro), 'names': sorted(x.name for x in t)}))"
)


class TestReadOnlyMode:
    def test_default_publishes_all_tools(self):
        info = json.loads(run_isolated(LIST_CODE))
        assert info["total"] == 58 and info["ro"] == 43

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE"])
    def test_write_tools_are_not_published_when_disabled(self, value):
        info = json.loads(run_isolated(LIST_CODE, ENABLE_WRITE_TOOLS=value))
        assert info["total"] == 43 and info["ro"] == 43
        for name in ("edit_rain_today", "update_station_settings", "clear_alarms", "trigger_ftp", "test_email",
                     "delete_diary", "upload_reports"):
            assert name not in info["names"], name
        assert "get_station_settings" in info["names"] and "daily_briefing" in info["names"]

    def test_disabled_functions_still_refuse_if_called_directly(self):
        out = run_isolated("import asyncio, server\nprint(asyncio.run(server.clear_error_log()))", ENABLE_WRITE_TOOLS="false")
        assert "deshabilitadas" in out


# ---------------------------------------------------------------- guía para agentes ----

class TestAgentGuidance:
    def test_server_instructions_cover_key_conventions(self):
        text = server.mcp.instructions
        assert text and len(text) < 2500
        for needle in ("daily_briefing", "anomaly", "frost_dates", "data_quality", "decimal commas", "-9999",
                       "confirmation", "ENABLE_WRITE_TOOLS=false", "snapshot"):
            assert needle in text, needle

    def test_instructions_only_reference_real_tools(self):
        import re
        registered = {t.name for t in __import__("asyncio").run(server.mcp.list_tools())}
        mentioned = set(re.findall(r"\b([a-z]+(?:_[a-z]+)+)\b", server.SERVER_INSTRUCTIONS))
        tool_like = {m for m in mentioned if m in registered or m.endswith(("_data", "_summary", "_records", "_dates",
                                                                              "_days", "_streaks", "_rose", "_quality",
                                                                              "_briefing", "_report", "_tags"))}
        assert tool_like <= registered, sorted(tool_like - registered)

    def test_new_prompts_are_registered_and_render(self):
        import asyncio
        names = {p.name for p in asyncio.run(server.mcp.list_prompts())}
        assert {"resumen_mensual", "heladas_y_cultivo", "auditoria_datos"} <= names
        assert "2025-03" in server.resumen_mensual_prompt(2025, 3) and "base 12.5" in server.heladas_y_cultivo_prompt(12.5)
        assert "data_quality" in server.auditoria_datos_prompt()


# ---------------------------------------------------------------- vía protocolo MCP ----

class TestNewToolsThroughMcpClient:
    @pytest.mark.asyncio
    async def test_new_tools_work_through_mcp_protocol(self):
        from fastmcp import Client

        rows = [dict(r, temp_max=15.0) for r in frost_year(2023, {"2023-04-10": -1, "2023-10-20": -2})]
        rows += month_rows(2023, 7, temp_media=20.0, dir_viento_dominante=90)
        with patch_dayfile(rows), patch.object(server, "_today", return_value=D(2024, 6, 1)):
            async with Client(server.mcp) as c:
                res = json.loads((await c.call_tool("frost_dates", {"start_year": 2023, "end_year": 2023})).content[0].text)
                assert res["anios"][0]["ultima_helada_primavera"] == "2023-04-10"
                res = json.loads((await c.call_tool("climatology", {})).content[0].text)
                assert res["meses"]["07"]["temp_media"] == 20.0
                res = json.loads((await c.call_tool("degree_days", {"start": "2023-01-01", "base": 4.0})).content[0].text)
                assert res["total"] > 0
                res = json.loads((await c.call_tool("wind_rose", {})).content[0].text)
                assert res["sector_mas_frecuente"] == "E"
                from fastmcp.exceptions import ToolError
                with pytest.raises(ToolError):
                    await c.call_tool("frost_dates", {"hemisphere": "east"})
