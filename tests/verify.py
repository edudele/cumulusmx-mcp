#!/usr/bin/env python3
"""Script de verificación directa de server.py (CumulusMX MCP).

Ejecuta las comprobaciones solicitadas:
1. Sintaxis e importación limpia de server.py.
2. Endpoint /health responde 200 sin autenticación.
3. Endpoint /mcp exige Bearer token cuando AUTH_TOKEN está configurado y rechaza peticiones sin él (401).
4. Tool rankings: probar cálculo de rankings con dataset simulado de dayfile (temp_min asc, lluvia_total desc, desempates, competition ranking 1, 2, 2, 4).
5. Tool year_records: probar cálculo de estadísticas de un año específico (días helada, extremos, totales).
6. Tool dayfile_range: probar alias (mintemp, maxtemp, rain) y summary_only=True.
7. FastMCP resources y prompts registrados correctamente.
"""
import asyncio
import datetime
import json
import os
import py_compile
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# Configurar token para verificación
TEST_TOKEN = "verify-secret-token-98765"
os.environ["MCP_AUTH_TOKEN"] = TEST_TOKEN

import server
from starlette.middleware import Middleware
from starlette.testclient import TestClient


def log_step(name: str, passed: bool, details: str = ""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"[{status}] {name}")
    if details:
        for line in details.strip().splitlines():
            print(f"       {line}")


def test_1_syntax_and_import() -> bool:
    name = "1. Sintaxis e importación limpia de server.py"
    try:
        server_path = PROJECT_DIR / "server.py"
        py_compile.compile(str(server_path), doraise=True)
        assert hasattr(server, "mcp")
        assert server.mcp.name == "cumulusmx-mcp"
        assert "fecha" in server.CANONICAL_COLUMNS
        assert len(server.DAYFILE_COLS) > 50
        assert "mintemp" in server.COLUMN_ALIASES
        log_step(name, True, f"server.py compilado e importado con éxito. Instancia FastMCP: '{server.mcp.name}'")
        return True
    except Exception as e:
        log_step(name, False, f"Error: {e}")
        return False


def test_2_health_endpoint() -> bool:
    name = "2. Endpoint /health responde 200 sin autenticación"
    try:
        app = server.mcp.http_app(
            path="/mcp",
            middleware=[
                Middleware(server.StripPrefix, prefix=server.STRIP_PREFIX),
                Middleware(server.BearerAuth),
            ],
        )
        with TestClient(app) as client:
            # GET /health sin cabecera Authorization
            r = client.get("/health")
            assert r.status_code == 200, f"Status code esperado 200, obtenido {r.status_code}"
            assert r.json() == {"status": "healthy", "service": "cumulusmx-mcp"}

            # HEAD /health
            r_head = client.head("/health")
            assert r_head.status_code == 200

            # Con prefijo externo (STRIP_PREFIX)
            prefix = server.STRIP_PREFIX.rstrip("/")
            r_prefix = client.get(f"{prefix}/health")
            assert r_prefix.status_code == 200

        log_step(name, True, "GET /health responde 200 {'status': 'healthy', 'service': 'cumulusmx-mcp'} sin autenticación")
        return True
    except Exception as e:
        log_step(name, False, f"Error: {e}")
        return False


def test_3_mcp_auth() -> bool:
    name = "3. Endpoint /mcp exige Bearer token cuando AUTH_TOKEN está configurado y rechaza peticiones sin él (401)"
    try:
        server.TOKEN = TEST_TOKEN.encode()
        app = server.mcp.http_app(
            path="/mcp",
            middleware=[
                Middleware(server.StripPrefix, prefix=server.STRIP_PREFIX),
                Middleware(server.BearerAuth),
            ],
        )
        with TestClient(app) as client:
            # Petición sin auth -> 401
            r_no_auth = client.get("/mcp")
            assert r_no_auth.status_code == 401, f"Sin token status: {r_no_auth.status_code}"
            assert r_no_auth.headers.get("WWW-Authenticate") == "Bearer"
            assert r_no_auth.json() == {"error": "unauthorized"}

            # Petición con token erróneo -> 401
            r_bad = client.get("/mcp", headers={"Authorization": "Bearer bad-token"})
            assert r_bad.status_code == 401

            # Petición con token válido -> BearerAuth permite el paso (status != 401)
            r_good = client.get("/mcp", headers={"Authorization": f"Bearer {TEST_TOKEN}"})
            assert r_good.status_code != 401, f"Con token válido status fue 401 inesperadamente"

            # Con prefijo strip prefix
            prefix = server.STRIP_PREFIX.rstrip("/")
            r_pref_unauth = client.get(f"{prefix}/mcp")
            assert r_pref_unauth.status_code == 401

        log_step(name, True, "Peticiones sin token o con token inválido devuelven 401 Unauthorized; token válido pasa autenticación.")
        return True
    except Exception as e:
        log_step(name, False, f"Error: {e}")
        return False


async def test_4_rankings_tool() -> bool:
    name = "4. Tool rankings: cálculo con dataset simulado (temp_min asc, lluvia_total desc, desempates, competition ranking 1, 2, 2, 4)"
    try:
        dataset = [
            {"fecha": "2024-01-01", "_date": datetime.date(2024, 1, 1), "temp_min": -5.0, "lluvia_total": 25.0},
            {"fecha": "2024-01-02", "_date": datetime.date(2024, 1, 2), "temp_min": -2.0, "lluvia_total": 15.0},
            {"fecha": "2024-01-03", "_date": datetime.date(2024, 1, 3), "temp_min": -2.0, "lluvia_total": 15.0},
            {"fecha": "2024-01-04", "_date": datetime.date(2024, 1, 4), "temp_min": 3.0, "lluvia_total": 5.0},
        ]

        # 1. temp_min asc
        r_tmin = server.rank(dataset, "temp_min", top=4, order="asc")
        positions_tmin = [x["posicion"] for x in r_tmin]
        assert positions_tmin == [1, 2, 2, 4], f"Posiciones esperadas [1, 2, 2, 4], obtenidas {positions_tmin}"
        # Desempate por fecha asc (2024-01-02 antes que 2024-01-03)
        assert r_tmin[1]["fecha"] == "2024-01-02"
        assert r_tmin[2]["fecha"] == "2024-01-03"

        # 2. lluvia_total desc
        r_rain = server.rank(dataset, "lluvia_total", top=4, order="desc")
        positions_rain = [x["posicion"] for x in r_rain]
        assert positions_rain == [1, 2, 2, 4], f"Posiciones esperadas [1, 2, 2, 4], obtenidas {positions_rain}"
        assert r_rain[1]["fecha"] == "2024-01-02"
        assert r_rain[2]["fecha"] == "2024-01-03"

        # 3. Llamada completa a la tool rankings()
        with patch.object(server, "_get_dayfile_rows", new_callable=AsyncMock) as mock_rows:
            mock_rows.return_value = dataset
            raw = await server.rankings("temp_min", top=4, order="asc", start_year=2024, end_year=2024)
            data = json.loads(raw)
            assert data["variable"] == "temp_min"
            assert data["unidad"] == "°C"
            assert [x["posicion"] for x in data["top"]] == [1, 2, 2, 4]

        log_step(name, True, "Rankings calculados correctamente con competition ranking [1, 2, 2, 4] y desempate por fecha ascendente.")
        return True
    except Exception as e:
        log_step(name, False, f"Error: {e}")
        return False


async def test_5_year_records_tool() -> bool:
    name = "5. Tool year_records: cálculo de estadísticas anuales (heladas, extremos, totales)"
    try:
        dataset = [
            {"fecha": "2024-01-01", "_date": datetime.date(2024, 1, 1), "temp_min": -3.0, "temp_max": 5.0, "lluvia_total": 0.0, "racha_max": 20.0},
            {"fecha": "2024-01-02", "_date": datetime.date(2024, 1, 2), "temp_min": 0.0, "temp_max": 10.0, "lluvia_total": 12.5, "racha_max": 55.0},
            {"fecha": "2024-07-15", "_date": datetime.date(2024, 7, 15), "temp_min": 15.0, "temp_max": 27.5, "lluvia_total": 0.1, "racha_max": 30.0},
            {"fecha": "2024-08-10", "_date": datetime.date(2024, 8, 10), "temp_min": 18.0, "temp_max": 34.0, "lluvia_total": 25.0, "racha_max": 40.0},
        ]
        # Días de helada (<= 0.0): 2 (-3.0 y 0.0)
        # Días cálidos: >=25: 2 (27.5 y 34.0); >=30: 1 (34.0)
        # Precipitación acumulada: 0.0 + 12.5 + 0.1 + 25.0 = 37.6 mm
        # Día más lluvioso: 25.0 mm el 2024-08-10
        # Días lluvia >= 0.2: 2 días (12.5 y 25.0)
        # Temp min abs: -3.0 el 2024-01-01; Temp max abs: 34.0 el 2024-08-10; Racha max: 55.0 el 2024-01-02
        stats = server.compute_year_records(dataset, 2024, datetime.date(2026, 10, 21))
        assert stats["dias_helada"] == 2
        assert stats["dias_calidos"] == {"gte_25": 2, "gte_30": 1}
        assert stats["precipitacion"]["acumulada_mm"] == 37.6
        assert stats["precipitacion"]["dia_mas_lluvioso"]["valor"] == 25.0
        assert stats["precipitacion"]["dia_mas_lluvioso"]["fecha"] == "2024-08-10"
        assert stats["precipitacion"]["dias_con_lluvia_gte_0_2"] == 2
        assert stats["temp_min_absoluta"]["valor"] == -3.0
        assert stats["temp_max_absoluta"]["valor"] == 34.0
        assert stats["racha_max"]["valor"] == 55.0

        with patch.object(server, "_all_dayfile", new_callable=AsyncMock) as mock_all:
            mock_all.return_value = dataset
            raw = await server.year_records(2024)
            data = json.loads(raw)
            assert data["year"] == 2024
            assert data["dias_helada"] == 2

        log_step(name, True, "year_records computa correctamente días helada (2), cálidos (2 gte_25, 1 gte_30), extremos y precipitación acumulada (37.6 mm).")
        return True
    except Exception as e:
        log_step(name, False, f"Error: {e}")
        return False


async def test_6_dayfile_range_tool() -> bool:
    name = "6. Tool dayfile_range: alias (mintemp, maxtemp, rain) y summary_only=True"
    try:
        # 1. Alias de columnas
        cols = server.resolve_columns(["mintemp", "maxtemp", "rain"])
        assert cols == ["fecha", "temp_min", "temp_max", "lluvia_total"], f"Columnas resueltas: {cols}"

        # 2. Case insensitive y espacios
        cols_ci = server.resolve_columns(["  MinTemp  ", "MAXTEMP", "Rain"])
        assert cols_ci == ["fecha", "temp_min", "temp_max", "lluvia_total"]

        dataset = [
            {"fecha": "2024-01-01", "_date": datetime.date(2024, 1, 1), "temp_min": -2.5, "temp_max": 8.0, "lluvia_total": 0.0},
            {"fecha": "2024-01-02", "_date": datetime.date(2024, 1, 2), "temp_min": 1.0, "temp_max": 12.0, "lluvia_total": 15.2},
        ]

        # 3. summary_only=True
        with patch.object(server, "_all_dayfile", new_callable=AsyncMock) as mock_all:
            mock_all.return_value = dataset
            raw_summary = await server.dayfile_range(
                start="2024-01-01",
                end="2024-01-02",
                columns=["mintemp", "maxtemp", "rain"],
                summary_only=True,
            )
            summ = json.loads(raw_summary)
            assert summ["n_dias"] == 2
            assert "temp_min" in summ["metricas"]
            assert "temp_max" in summ["metricas"]
            assert "lluvia_total" in summ["metricas"]
            assert summ["metricas"]["lluvia_total"]["suma"] == 15.2
            assert summ["metricas"]["temp_min"]["min"]["valor"] == -2.5

            # 4. summary_only=False (filas proyectadas)
            raw_rows = await server.dayfile_range(
                start="2024-01-01",
                end="2024-01-02",
                columns=["mintemp", "rain"],
                summary_only=False,
            )
            rows = json.loads(raw_rows)
            assert isinstance(rows, list)
            assert len(rows) == 2
            assert set(rows[0].keys()) == {"fecha", "temp_min", "lluvia_total"}

        log_step(name, True, "dayfile_range resuelve alias mintemp, maxtemp, rain y procesa summary_only=True y proyección de columnas correctamente.")
        return True
    except Exception as e:
        log_step(name, False, f"Error: {e}")
        return False


async def test_7_resources_and_prompts() -> bool:
    name = "7. FastMCP resources y prompts registrados correctamente"
    try:
        # Recursos
        resources = await server.mcp.list_resources()
        registered_uris = {str(r.uri) for r in resources}
        expected_uris = {
            "weather://current",
            "weather://today",
            "weather://station",
            "weather://records/alltime",
        }
        assert expected_uris.issubset(registered_uris), f"Faltan recursos: {expected_uris - registered_uris}"

        # Prompts
        prompts = await server.mcp.list_prompts()
        registered_prompts = {p.name for p in prompts}
        expected_prompts = {
            "resumen_diario",
            "analisis_extremos",
            "comparativa_periodos",
            "resumen_anual",
        }
        assert expected_prompts.issubset(registered_prompts), f"Faltan prompts: {expected_prompts - registered_prompts}"

        # Ejecutar render de prompts
        p1 = await server.mcp.get_prompt("resumen_diario")
        r1 = await p1.render({"date": "2024-01-01"})
        assert "2024-01-01" in r1.messages[0].content.text

        p2 = await server.mcp.get_prompt("analisis_extremos")
        r2 = await p2.render({"variable": "temp_max", "top": 5})
        assert "temp_max" in r2.messages[0].content.text

        p3 = await server.mcp.get_prompt("comparativa_periodos")
        r3 = await p3.render({"start_a": "2023-01-01", "end_a": "2023-01-10", "start_b": "2024-01-01", "end_b": "2024-01-10"})
        assert "2023-01-01" in r3.messages[0].content.text

        p4 = await server.mcp.get_prompt("resumen_anual")
        r4 = await p4.render({"year": 2024})
        assert "2024" in r4.messages[0].content.text

        log_step(name, True, f"4 recursos weather:// y 4 prompts verificados y registrados en FastMCP.")
        return True
    except Exception as e:
        log_step(name, False, f"Error: {e}")
        return False


async def run_all():
    print("=" * 70)
    print("EJECUTANDO BATERÍA DE VERIFICACIÓN DE CUMULUSMX-MCP")
    print("=" * 70)

    results = []
    results.append(test_1_syntax_and_import())
    results.append(test_2_health_endpoint())
    results.append(test_3_mcp_auth())
    results.append(await test_4_rankings_tool())
    results.append(await test_5_year_records_tool())
    results.append(await test_6_dayfile_range_tool())
    results.append(await test_7_resources_and_prompts())

    print("=" * 70)
    passed_count = sum(1 for r in results if r)
    total_count = len(results)
    success = passed_count == total_count
    print(f"RESULTADO: {passed_count}/{total_count} bloques superados ({'100% PASS' if success else 'FAIL'})")
    print("=" * 70)
    return 0 if success else 1


if __name__ == "__main__":
    code = asyncio.run(run_all())
    sys.exit(code)
