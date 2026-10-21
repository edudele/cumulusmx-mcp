"""Batería de pruebas automatizadas para server.py (CumulusMX MCP).

Verifica:
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
import math
import os
import py_compile
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx
from starlette.middleware import Middleware
from starlette.testclient import TestClient

# Asegurar que el directorio del proyecto esté en sys.path
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# Configurar token de autenticación para las pruebas antes de importar server
TEST_TOKEN = "test-secret-token-12345"
os.environ["MCP_AUTH_TOKEN"] = TEST_TOKEN

import server  # noqa: E402


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def test_app():
    """Instancia de la aplicación ASGI con los middlewares de prefijo y autenticación."""
    server.TOKEN = TEST_TOKEN.encode()
    return server.mcp.http_app(
        path="/mcp",
        middleware=[
            Middleware(server.StripPrefix, prefix=server.STRIP_PREFIX),
            Middleware(server.BearerAuth),
        ],
    )


@pytest.fixture
def client(test_app):
    """Cliente de pruebas Starlette con gestión de contexto (lifespan FastMCP)."""
    with TestClient(test_app) as c:
        yield c


@pytest.fixture
def sample_dayfile_rows():
    """Dataset simulado con casos controlados para rankings y récords anuales."""
    return [
        {
            "_date": datetime.date(2024, 1, 1),
            "fecha": "2024-01-01",
            "temp_min": -5.0,
            "temp_max": 8.0,
            "temp_media": 1.5,
            "lluvia_total": 25.0,
            "racha_max": 35.0,
            "pres_min": 1010.0,
            "pres_max": 1025.0,
            "hum_min": 40.0,
            "hum_max": 90.0,
            "horas_sol": 4.5,
        },
        {
            "_date": datetime.date(2024, 1, 2),
            "fecha": "2024-01-02",
            "temp_min": -2.0,
            "temp_max": 12.0,
            "temp_media": 5.0,
            "lluvia_total": 15.0,
            "racha_max": 65.0,
            "pres_min": 1005.0,
            "pres_max": 1018.0,
            "hum_min": 50.0,
            "hum_max": 95.0,
            "horas_sol": 2.0,
        },
        {
            "_date": datetime.date(2024, 1, 3),
            "fecha": "2024-01-03",
            "temp_min": -2.0,  # Empate con 2024-01-02 en temp_min y lluvia_total
            "temp_max": 14.0,
            "temp_media": 6.0,
            "lluvia_total": 15.0,
            "racha_max": 40.0,
            "pres_min": 1012.0,
            "pres_max": 1022.0,
            "hum_min": 55.0,
            "hum_max": 85.0,
            "horas_sol": 6.0,
        },
        {
            "_date": datetime.date(2024, 1, 4),
            "fecha": "2024-01-04",
            "temp_min": 3.0,
            "temp_max": 27.0,  # Día cálido >= 25°C
            "temp_media": 15.0,
            "lluvia_total": 5.0,
            "racha_max": 20.0,
            "pres_min": 1015.0,
            "pres_max": 1026.0,
            "hum_min": 35.0,
            "hum_max": 80.0,
            "horas_sol": 9.5,
        },
        {
            "_date": datetime.date(2024, 8, 10),
            "fecha": "2024-08-10",
            "temp_min": 16.0,
            "temp_max": 34.5,  # Día cálido >= 25°C y >= 30°C
            "temp_media": 25.2,
            "lluvia_total": 0.1,  # < 0.2 mm (no cuenta como día con lluvia)
            "racha_max": 28.0,
            "pres_min": 1014.0,
            "pres_max": 1020.0,
            "hum_min": 25.0,
            "hum_max": 65.0,
            "horas_sol": 12.0,
        },
    ]


# =====================================================================
# 1. Sintaxis e importación limpia de server.py
# =====================================================================

class TestImportAndSyntax:
    def test_py_compile_syntax(self):
        """Verifica que server.py compila sin errores de sintaxis."""
        server_path = PROJECT_DIR / "server.py"
        compiled = py_compile.compile(str(server_path), doraise=True)
        assert compiled is not None

    def test_module_clean_import(self):
        """Verifica que el módulo server expone la instancia mcp y constantes clave."""
        assert hasattr(server, "mcp")
        assert server.mcp.name == "cumulusmx-mcp"
        assert isinstance(server.CANONICAL_COLUMNS, tuple)
        assert "fecha" in server.CANONICAL_COLUMNS
        assert isinstance(server.COLUMN_ALIASES, dict)
        assert isinstance(server.UNITS, dict)
        assert isinstance(server.ADDITIVE_COLUMNS, frozenset)

    def test_num_parsing_helper(self):
        """Verifica la robustez de _num ante valores nulos, sentinelas o no numéricos."""
        assert server._num(12.5) == 12.5
        assert server._num("12.5") == 12.5
        assert server._num(0) == 0.0
        assert server._num(None) is None
        assert server._num("") is None
        assert server._num("-") is None
        assert server._num("null") is None
        assert server._num("None") is None
        assert server._num(-9999) is None
        assert server._num(-10000) is None
        assert server._num(float("nan")) is None
        assert server._num("invalid_string") is None


# =====================================================================
# 2. Endpoint /health responde 200 sin autenticación
# =====================================================================

class TestHealthEndpoint:
    def test_health_returns_200_without_auth(self, client):
        """Verifica que GET /health responde 200 OK sin cabecera Authorization."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "healthy", "service": "cumulusmx-mcp"}

    def test_health_head_method(self, client):
        """Verifica que HEAD /health responde 200 OK sin cabecera Authorization."""
        resp = client.head("/health")
        assert resp.status_code == 200

    def test_health_with_strip_prefix(self, client):
        """Verifica que el endpoint /health también funciona tras el prefijo externo (STRIP_PREFIX)."""
        prefix = server.STRIP_PREFIX.rstrip("/")
        resp = client.get(f"{prefix}/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy", "service": "cumulusmx-mcp"}


# =====================================================================
# 3. Endpoint /mcp exige Bearer token cuando AUTH_TOKEN está configurado
# =====================================================================

class TestAuthMiddleware:
    def test_mcp_unauthorized_without_header(self, client):
        """Verifica que peticiones a /mcp sin cabecera Authorization devuelven 401."""
        resp = client.get("/mcp")
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate") == "Bearer"
        assert resp.json() == {"error": "unauthorized"}

    def test_mcp_unauthorized_with_invalid_token(self, client):
        """Verifica que peticiones a /mcp con Bearer token incorrecto devuelven 401."""
        resp = client.get("/mcp", headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate") == "Bearer"
        assert resp.json() == {"error": "unauthorized"}

    def test_mcp_unauthorized_with_wrong_scheme(self, client):
        """Verifica que esquemas de autenticación distintos de Bearer son rechazados con 401."""
        resp = client.get("/mcp", headers={"Authorization": f"Basic {TEST_TOKEN}"})
        assert resp.status_code == 401

    def test_mcp_authorized_with_valid_token(self, client):
        """Verifica que el Bearer token válido pasa el middleware de autenticación (no 401)."""
        resp = client.get("/mcp", headers={"Authorization": f"Bearer {TEST_TOKEN}"})
        # Si pasa la autenticación, FastMCP maneja la petición (status no es 401)
        assert resp.status_code != 401

    def test_strip_prefix_with_mcp_endpoint(self, client):
        """Verifica que el StripPrefix middleware procesa la ruta antes de la autenticación."""
        prefix = server.STRIP_PREFIX.rstrip("/")
        # Sin token -> 401
        resp_no_auth = client.get(f"{prefix}/mcp")
        assert resp_no_auth.status_code == 401

        # Con token correcto -> no 401
        resp_auth = client.get(f"{prefix}/mcp", headers={"Authorization": f"Bearer {TEST_TOKEN}"})
        assert resp_auth.status_code != 401


# =====================================================================
# 4. Tool rankings: cálculo de rankings con dataset simulado
# =====================================================================

class TestRankingsTool:
    def test_rank_temp_min_asc_with_ties(self, sample_dayfile_rows):
        """Verifica competition ranking (1, 2, 2, 4) y desempate por fecha asc para temp_min asc."""
        # Valores en el dataset:
        # 2024-01-01: -5.0
        # 2024-01-02: -2.0
        # 2024-01-03: -2.0
        # 2024-01-04:  3.0
        # 2024-08-10: 16.0
        ranked = server.rank(sample_dayfile_rows, "temp_min", top=4, order="asc")

        assert len(ranked) == 4
        # 1er puesto: -5.0 el 2024-01-01
        assert ranked[0] == {"posicion": 1, "fecha": "2024-01-01", "valor": -5.0}
        # 2º puesto empatado: fecha más antigua primero (2024-01-02 antes que 2024-01-03)
        assert ranked[1] == {"posicion": 2, "fecha": "2024-01-02", "valor": -2.0}
        assert ranked[2] == {"posicion": 2, "fecha": "2024-01-03", "valor": -2.0}
        # 4º puesto (competition ranking salta la posición 3 al haber 2 segundos):
        assert ranked[3] == {"posicion": 4, "fecha": "2024-01-04", "valor": 3.0}

    def test_rank_lluvia_total_desc_with_ties(self, sample_dayfile_rows):
        """Verifica competition ranking (1, 2, 2, 4) y desempate por fecha asc para lluvia_total desc."""
        # Valores en el dataset:
        # 2024-01-01: 25.0
        # 2024-01-02: 15.0
        # 2024-01-03: 15.0
        # 2024-01-04:  5.0
        # 2024-08-10:  0.1
        ranked = server.rank(sample_dayfile_rows, "lluvia_total", top=4, order="desc")

        assert len(ranked) == 4
        # 1º: 25.0 el 2024-01-01
        assert ranked[0] == {"posicion": 1, "fecha": "2024-01-01", "valor": 25.0}
        # 2º empatado: 15.0 el 2024-01-02 (fecha anterior a 2024-01-03)
        assert ranked[1] == {"posicion": 2, "fecha": "2024-01-02", "valor": 15.0}
        assert ranked[2] == {"posicion": 2, "fecha": "2024-01-03", "valor": 15.0}
        # 4º: 5.0 el 2024-01-04
        assert ranked[3] == {"posicion": 4, "fecha": "2024-01-04", "valor": 5.0}

    def test_rank_three_way_tie(self):
        """Verifica competition ranking con triple empate (1, 2, 2, 2, 5)."""
        rows = [
            {"fecha": "2024-02-01", "temp_max": 20.0},
            {"fecha": "2024-02-02", "temp_max": 18.0},
            {"fecha": "2024-02-03", "temp_max": 18.0},
            {"fecha": "2024-02-04", "temp_max": 18.0},
            {"fecha": "2024-02-05", "temp_max": 10.0},
        ]
        ranked = server.rank(rows, "temp_max", top=5, order="desc")
        assert [r["posicion"] for r in ranked] == [1, 2, 2, 2, 5]
        assert [r["fecha"] for r in ranked[1:4]] == ["2024-02-02", "2024-02-03", "2024-02-04"]

    @pytest.mark.asyncio
    async def test_rankings_tool_async_execution(self, sample_dayfile_rows):
        """Verifica la ejecución de la tool rankings con llamada async y respuesta JSON completa."""
        with patch.object(server, "_get_dayfile_rows", new_callable=AsyncMock) as mock_rows:
            mock_rows.return_value = sample_dayfile_rows

            raw_res = await server.rankings(
                variable="temp_min",
                top=4,
                order="asc",
                start_year=2024,
                end_year=2024,
            )
            res = json.loads(raw_res)

            assert res["variable"] == "temp_min"
            assert res["unidad"] == "°C"
            assert res["orden"] == "asc"
            assert res["periodo"] == {"start_year": 2024, "end_year": 2024}
            assert len(res["top"]) == 4
            assert res["top"][0]["posicion"] == 1
            assert res["top"][1]["posicion"] == 2
            assert res["top"][2]["posicion"] == 2
            assert res["top"][3]["posicion"] == 4

    @pytest.mark.asyncio
    async def test_rankings_validations(self):
        """Verifica validación de argumentos en la tool rankings."""
        with pytest.raises(ValueError, match="no válida"):
            await server.rankings(variable="invalid_var")  # type: ignore

        with pytest.raises(ValueError, match="order debe ser 'asc' o 'desc'"):
            await server.rankings(variable="temp_min", order="diagonal")  # type: ignore

        with pytest.raises(ValueError, match="start_year no puede ser posterior a end_year"):
            await server.rankings(variable="temp_min", start_year=2025, end_year=2024)


# =====================================================================
# 5. Tool year_records: cálculo de estadísticas de un año específico
# =====================================================================

class TestYearRecordsTool:
    def test_compute_year_records_calculation(self, sample_dayfile_rows):
        """Verifica el cálculo de días de helada, días cálidos, extremos y totales."""
        # En sample_dayfile_rows:
        # Heladas (temp_min <= 0.0):
        #   2024-01-01: -5.0 (helada)
        #   2024-01-02: -2.0 (helada)
        #   2024-01-03: -2.0 (helada)
        #   2024-01-04:  3.0 (no)
        #   2024-08-10: 16.0 (no) -> Total: 3 días de helada
        # Cálidos:
        #   gte_25 (temp_max >= 25.0): 2024-01-04 (27.0), 2024-08-10 (34.5) -> 2 días
        #   gte_30 (temp_max >= 30.0): 2024-08-10 (34.5) -> 1 día
        # Precipitaciones:
        #   Total mm: 25.0 + 15.0 + 15.0 + 5.0 + 0.1 = 60.1 mm
        #   Día más lluvioso: 25.0 mm el 2024-01-01
        #   Días lluvia >= 0.2 mm: 4 días (25.0, 15.0, 15.0, 5.0)
        # Racha máxima: 65.0 km/h el 2024-01-02
        # Temp min absoluta: -5.0 °C el 2024-01-01
        # Temp max absoluta: 34.5 °C el 2024-08-10
        today = datetime.date(2026, 10, 21)
        res = server.compute_year_records(sample_dayfile_rows, 2024, today)

        assert res["year"] == 2024
        assert res["parcial"] is False  # 2024 != 2026
        assert res["n_dias_con_datos"] == 5

        # Días helada y cálidos
        assert res["dias_helada"] == 3
        assert res["dias_calidos"] == {"gte_25": 2, "gte_30": 1}

        # Extremos
        assert res["temp_min_absoluta"]["valor"] == -5.0
        assert res["temp_min_absoluta"]["fecha"] == "2024-01-01"
        assert res["temp_max_absoluta"]["valor"] == 34.5
        assert res["temp_max_absoluta"]["fecha"] == "2024-08-10"
        assert res["racha_max"]["valor"] == 65.0
        assert res["racha_max"]["fecha"] == "2024-01-02"

        # Precipitaciones
        precip = res["precipitacion"]
        assert precip["acumulada_mm"] == 60.1
        assert precip["dia_mas_lluvioso"]["valor"] == 25.0
        assert precip["dia_mas_lluvioso"]["fecha"] == "2024-01-01"
        assert precip["dias_con_lluvia_gte_0_2"] == 4
        assert precip["n_dias_con_dato"] == 5

    def test_compute_year_records_parcial_flag(self, sample_dayfile_rows):
        """Verifica que el flag 'parcial' es True si el año coincide con el actual."""
        today_same_year = datetime.date(2024, 6, 1)
        res = server.compute_year_records(sample_dayfile_rows, 2024, today_same_year)
        assert res["parcial"] is True

    @pytest.mark.asyncio
    async def test_year_records_tool_async_execution(self, sample_dayfile_rows):
        """Verifica la ejecución de la tool year_records."""
        with patch.object(server, "_all_dayfile", new_callable=AsyncMock) as mock_all:
            mock_all.return_value = sample_dayfile_rows

            raw_res = await server.year_records(2024)
            res = json.loads(raw_res)

            assert res["year"] == 2024
            assert res["dias_helada"] == 3
            assert res["precipitacion"]["acumulada_mm"] == 60.1

    @pytest.mark.asyncio
    async def test_year_records_validations(self):
        """Verifica validación de rango de año y año sin datos."""
        with pytest.raises(ValueError, match="year fuera de rango"):
            await server.year_records(1999)

        with pytest.raises(ValueError, match="year fuera de rango"):
            await server.year_records(2051)

        with patch.object(server, "_all_dayfile", new_callable=AsyncMock) as mock_all:
            mock_all.return_value = []
            with pytest.raises(ValueError, match="No hay datos registrados"):
                await server.year_records(2024)


# =====================================================================
# 6. Tool dayfile_range: alias y summary_only=True
# =====================================================================

class TestDayfileRangeTool:
    def test_resolve_columns_aliases(self):
        """Verifica la resolución canónica de alias (mintemp, maxtemp, rain)."""
        resolved = server.resolve_columns(["mintemp", "maxtemp", "rain"])
        # 'fecha' siempre va en primera posición
        assert resolved == ["fecha", "temp_min", "temp_max", "lluvia_total"]

    def test_resolve_columns_case_insensitive_and_whitespace(self):
        """Verifica que tolera mayúsculas/minúsculas y espacios en blanco."""
        resolved = server.resolve_columns(["  MinTemp  ", "MAXTEMP", " Rain "])
        assert resolved == ["fecha", "temp_min", "temp_max", "lluvia_total"]

    def test_resolve_columns_default_canonical(self):
        """Verifica que None o lista vacía devuelve todas las columnas canónicas."""
        assert server.resolve_columns(None) == list(server.CANONICAL_COLUMNS)
        assert server.resolve_columns([]) == list(server.CANONICAL_COLUMNS)

    def test_resolve_columns_unknown_column_raises(self):
        """Verifica que columnas desconocidas lanzan ValueError con la lista de válidas."""
        with pytest.raises(ValueError, match="Columnas desconocidas"):
            server.resolve_columns(["mintemp", "columna_inexistente_xyz"])

    def test_summarize_additive_and_non_additive(self, sample_dayfile_rows):
        """Verifica que summarize calcula agregados y solo incluye 'suma' en aditivas."""
        cols = ["fecha", "temp_min", "lluvia_total"]
        summ = server.summarize(sample_dayfile_rows, cols)

        assert summ["n_dias"] == 5
        assert summ["desde"] == "2024-01-01"
        assert summ["hasta"] == "2024-08-10"

        # Columna no aditiva (temp_min) -> NO debe tener clave 'suma'
        m_tmin = summ["metricas"]["temp_min"]
        assert m_tmin["unidad"] == "°C"
        assert m_tmin["min"]["valor"] == -5.0
        assert m_tmin["max"]["valor"] == 16.0
        assert "suma" not in m_tmin

        # Columna aditiva (lluvia_total) -> DEBE tener clave 'suma'
        m_rain = summ["metricas"]["lluvia_total"]
        assert m_rain["unidad"] == "mm"
        assert m_rain["suma"] == 60.1

    @pytest.mark.asyncio
    async def test_dayfile_range_summary_only_true(self, sample_dayfile_rows):
        """Verifica dayfile_range con alias y summary_only=True."""
        with patch.object(server, "_all_dayfile", new_callable=AsyncMock) as mock_all:
            mock_all.return_value = sample_dayfile_rows

            raw_res = await server.dayfile_range(
                start="2024-01-01",
                end="2024-01-04",
                columns=["mintemp", "maxtemp", "rain"],
                summary_only=True,
            )
            res = json.loads(raw_res)

            assert res["n_dias"] == 4
            assert res["desde"] == "2024-01-01"
            assert res["hasta"] == "2024-01-04"
            assert "metricas" in res
            assert "temp_min" in res["metricas"]
            assert "temp_max" in res["metricas"]
            assert "lluvia_total" in res["metricas"]
            assert "filas" not in res

    @pytest.mark.asyncio
    async def test_dayfile_range_rows_projection(self, sample_dayfile_rows):
        """Verifica dayfile_range proyectando solo las columnas solicitadas con alias."""
        with patch.object(server, "_all_dayfile", new_callable=AsyncMock) as mock_all:
            mock_all.return_value = sample_dayfile_rows

            raw_res = await server.dayfile_range(
                start="2024-01-01",
                end="2024-01-02",
                columns=["mintemp", "rain"],
                summary_only=False,
            )
            res = json.loads(raw_res)

            assert isinstance(res, list)
            assert len(res) == 2
            first = res[0]
            assert set(first.keys()) == {"fecha", "temp_min", "lluvia_total"}
            assert first["temp_min"] == -5.0
            assert first["lluvia_total"] == 25.0

    @pytest.mark.asyncio
    async def test_dayfile_range_validations(self):
        """Verifica control de errores en fechas de dayfile_range."""
        with pytest.raises(ValueError, match="start inválida"):
            await server.dayfile_range(start="01-01-2024", end="2024-01-10")

        with pytest.raises(ValueError, match="end inválida"):
            await server.dayfile_range(start="2024-01-01", end="invalid-date")

        with pytest.raises(ValueError, match="end anterior a start"):
            await server.dayfile_range(start="2024-01-10", end="2024-01-01")


# =====================================================================
# 7. FastMCP resources y prompts registrados correctamente
# =====================================================================

class TestFastMCPResourcesAndPrompts:
    @pytest.mark.asyncio
    async def test_resources_registered(self):
        """Verifica que los 4 recursos weather:// están registrados en FastMCP."""
        resources = await server.mcp.list_resources()
        registered_uris = {str(r.uri) for r in resources}
        expected_uris = {
            "weather://current",
            "weather://today",
            "weather://station",
            "weather://records/alltime",
        }
        assert expected_uris.issubset(registered_uris)

    @pytest.mark.asyncio
    async def test_resources_read_execution(self):
        """Verifica la ejecución de cada recurso con respuestas HTTP simuladas."""
        # weather://current llama a current_data -> /api/data/currentdata
        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = '{"temp": 12.3}'
            res_current = await server.current_weather_resource()
            assert res_current == '{"temp": 12.3}'

        # weather://today llama a today_all -> /api/todayyest/*.json
        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = '{"temp": 15.0}'
            res_today = await server.today_weather_resource()
            data = json.loads(res_today)
            assert "temp" in data

        # weather://station llama a station_info -> /api/info/version.json y units.json
        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ['{"Version": "3.28"}', '{"temp": "C"}']
            res_station = await server.station_info_resource()
            data = json.loads(res_station)
            assert data["version"] == {"Version": "3.28"}
            assert data["units"] == {"temp": "C"}

        # weather://records/alltime llama a records_all(scope="alltime")
        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = '{"records": "ok"}'
            res_records = await server.records_alltime_resource()
            data = json.loads(res_records)
            assert "temperature" in data

    @pytest.mark.asyncio
    async def test_prompts_registered(self):
        """Verifica que los 4 prompts están registrados en FastMCP."""
        prompts = await server.mcp.list_prompts()
        registered_names = {p.name for p in prompts}
        expected_names = {
            "resumen_diario",
            "analisis_extremos",
            "comparativa_periodos",
            "resumen_anual",
        }
        assert expected_names.issubset(registered_names)

    @pytest.mark.asyncio
    async def test_prompts_rendering(self):
        """Verifica la renderización correcta de texto de cada prompt."""
        # 1. resumen_diario
        p_diario = await server.mcp.get_prompt("resumen_diario")
        assert p_diario is not None
        rendered = await p_diario.render({"date": "2024-05-15"})
        content = rendered.messages[0].content.text
        assert "2024-05-15" in content
        assert "Temperaturas" in content

        # 2. analisis_extremos
        p_extremos = await server.mcp.get_prompt("analisis_extremos")
        assert p_extremos is not None
        rendered = await p_extremos.render({"variable": "racha_max", "top": 5})
        content = rendered.messages[0].content.text
        assert "racha_max" in content
        assert "top 5" in content

        # 3. comparativa_periodos
        p_comp = await server.mcp.get_prompt("comparativa_periodos")
        assert p_comp is not None
        rendered = await p_comp.render({
            "start_a": "2023-01-01",
            "end_a": "2023-01-31",
            "start_b": "2024-01-01",
            "end_b": "2024-01-31",
        })
        content = rendered.messages[0].content.text
        assert "2023-01-01" in content
        assert "2024-01-31" in content

        # 4. resumen_anual
        p_anual = await server.mcp.get_prompt("resumen_anual")
        assert p_anual is not None
        rendered = await p_anual.render({"year": 2024})
        content = rendered.messages[0].content.text
        assert "2024" in content
        assert "year_records" in content


# =====================================================================
# 8. Verificación de corrección de bugs y casos límite adicionales
# =====================================================================

class TestBugFixesAndEdgeCases:
    @pytest.mark.asyncio
    async def test_graph_summary_points_signature_fixed(self):
        """Verifica que graph_summary admite points=0 y points>0 sin error NameError."""
        fake_graph_data = {
            "temp": [
                [1704067200000, 10.5],
                [1704070800000, 12.0],
                [1704074400000, 9.8],
            ]
        }
        with patch.object(server, "_json", new_callable=AsyncMock) as mock_json:
            mock_json.return_value = fake_graph_data

            # Sin puntos reducidos (points=0 por defecto)
            res_str = await server.graph_summary("tempdata", points=0)
            res = json.loads(res_str)
            assert "temp" in res
            assert res["temp"]["min"] == 9.8
            assert res["temp"]["max"] == 12.0
            assert "serie" not in res["temp"]

            # Con puntos reducidos (points > 0)
            res_str_pts = await server.graph_summary("tempdata", points=2)
            res_pts = json.loads(res_str_pts)
            assert "serie" in res_pts["temp"]
            assert len(res_pts["temp"]["serie"]) > 0

    def test_extreme_helper_empty_and_ties(self):
        """Verifica el comportamiento de la función extreme() con empates y listas vacías."""
        assert server.extreme([], "temp_min", "min") is None

        # Empates: devuelve el valor, la fecha más antigua y todas las fechas empatadas
        rows = [
            {"fecha": "2024-03-05", "temp_min": -4.0},
            {"fecha": "2024-01-10", "temp_min": -4.0},
            {"fecha": "2024-02-20", "temp_min": -2.0},
        ]
        ext = server.extreme(rows, "temp_min", "min")
        assert ext is not None
        assert ext["valor"] == -4.0
        assert ext["fecha"] == "2024-01-10"  # Más antigua
        assert ext["fechas"] == ["2024-01-10", "2024-03-05"]

    def test_rank_first_place_tie(self):
        """Verifica competition ranking cuando el empate ocurre en el 1er puesto (1, 1, 3, 4)."""
        rows = [
            {"fecha": "2024-05-02", "racha_max": 80.0},
            {"fecha": "2024-05-01", "racha_max": 80.0},
            {"fecha": "2024-05-03", "racha_max": 60.0},
            {"fecha": "2024-05-04", "racha_max": 40.0},
        ]
        ranked = server.rank(rows, "racha_max", top=4, order="desc")
        assert [r["posicion"] for r in ranked] == [1, 1, 3, 4]
        # Desempate por fecha asc (2024-05-01 antes que 2024-05-02)
        assert ranked[0]["fecha"] == "2024-05-01"
        assert ranked[1]["fecha"] == "2024-05-02"

    def test_resolve_columns_expanded_aliases(self):
        """Verifica cobertura exhaustiva de los alias de columnas meteorológicas."""
        aliases = [
            "date", "avgtemp", "precipitation", "precip", "wind",
            "gust", "windgust", "press", "pressure", "minpress",
            "maxpress", "hum", "humidity", "minhum", "maxhum",
            "sun", "sunhours", "sol", "solar", "uv", "et",
        ]
        resolved = server.resolve_columns(aliases)
        assert resolved[0] == "fecha"
        expected_canons = {
            "temp_media", "lluvia_total", "racha_max", "pres_min",
            "pres_max", "hum_min", "hum_max", "horas_sol", "solar_max",
            "uv_max", "ET",
        }
        assert expected_canons.issubset(set(resolved))


# =====================================================================
# 9. Verificación de caché de dayfile y paginación cruda
# =====================================================================

class TestDayfileCacheAndRawFetching:
    @pytest.mark.asyncio
    async def test_get_dayfile_rows_caching(self, sample_dayfile_rows):
        """Verifica que _get_dayfile_rows utiliza la caché en memoria y respeta el TTL."""
        server._cached_dayfile_by_year.clear()
        server._dayfile_cache_time = 0.0

        with patch.object(server, "_fetch_all_dayfile_raw", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = sample_dayfile_rows

            # Primera llamada: debe consultar la fuente y rellenar caché
            rows_1 = await server._get_dayfile_rows(start_year=2024, end_year=2024)
            assert len(rows_1) == 5
            assert mock_fetch.call_count == 1
            assert 2024 in server._cached_dayfile_by_year

            # Segunda llamada dentro del TTL: debe servir de la caché sin llamar a fetch
            rows_2 = await server._get_dayfile_rows(start_year=2024, end_year=2024)
            assert len(rows_2) == 5
            assert mock_fetch.call_count == 1  # No se ha vuelto a llamar

    @pytest.mark.asyncio
    async def test_get_dayfile_rows_filtering_by_year(self, sample_dayfile_rows):
        """Verifica que el filtrado por año devuelve únicamente los registros del rango."""
        server._cached_dayfile_by_year.clear()
        server._dayfile_cache_time = 0.0

        # Crear filas en dos años distintos: 2023 y 2024
        row_2023 = dict(sample_dayfile_rows[0])
        row_2023["_date"] = datetime.date(2023, 5, 1)
        row_2023["fecha"] = "2023-05-01"

        with patch.object(server, "_fetch_all_dayfile_raw", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [row_2023, *sample_dayfile_rows]

            rows_2023 = await server._get_dayfile_rows(start_year=2023, end_year=2023)
            assert len(rows_2023) == 1
            assert rows_2023[0]["fecha"] == "2023-05-01"

            rows_2024 = await server._get_dayfile_rows(start_year=2024, end_year=2024)
            assert len(rows_2024) == 5

    @pytest.mark.asyncio
    async def test_fetch_all_dayfile_raw_pagination_and_date_formats(self):
        """Verifica la paginación y el parseo de diferentes formatos de fecha en dayfile."""
        # Simula 2 páginas de la API de CumulusMX
        p1: list[Any] = [None] * len(server.DAYFILE_COLS)
        p1[1] = "01/01/24"  # Formato %d/%m/%y
        p1[5] = "-3.5"      # temp_min

        p2: list[Any] = [None] * len(server.DAYFILE_COLS)
        p2[1] = "2024-01-02"  # Formato %Y-%m-%d
        p2[5] = "2.0"

        with patch.object(server, "_json", new_callable=AsyncMock) as mock_json:
            mock_json.side_effect = [
                {"draw": 1, "recordsTotal": 501, "data": [p1]},
                {"draw": 1, "recordsTotal": 501, "data": [p2]},
                {"draw": 1, "recordsTotal": 501, "data": []},
            ]

            rows = await server._fetch_all_dayfile_raw()
            assert len(rows) == 2
            assert rows[0]["_date"] == datetime.date(2024, 1, 1)
            assert rows[0]["fecha"] == "2024-01-01"
            assert rows[1]["_date"] == datetime.date(2024, 1, 2)
            assert rows[1]["fecha"] == "2024-01-02"


# =====================================================================
# 10. Verificación de tools analíticas adicionales
# =====================================================================

class TestAdditionalAnalyticsTools:
    @pytest.mark.asyncio
    async def test_climate_summary_tool(self, sample_dayfile_rows):
        """Verifica la tool climate_summary para un rango de fechas."""
        with patch.object(server, "_all_dayfile", new_callable=AsyncMock) as mock_all:
            mock_all.return_value = sample_dayfile_rows

            raw_res = await server.climate_summary("2024-01-01", "2024-01-03")
            res = json.loads(raw_res)

            assert res["dias"] == 3
            assert res["desde"] == "2024-01-01"
            assert res["hasta"] == "2024-01-03"
            assert res["temp_min_absoluta"]["valor"] == -5.0
            assert res["lluvia_total"] == 55.0

    @pytest.mark.asyncio
    async def test_monthly_summary_tool(self, sample_dayfile_rows):
        """Verifica la tool monthly_summary para un año específico."""
        with patch.object(server, "_all_dayfile", new_callable=AsyncMock) as mock_all:
            mock_all.return_value = sample_dayfile_rows

            raw_res = await server.monthly_summary(2024)
            res = json.loads(raw_res)

            # Enero tiene 4 días registrados, Agosto tiene 1 día
            assert "2024-01" in res
            assert res["2024-01"]["dias"] == 4
            assert "2024-08" in res
            assert res["2024-08"]["dias"] == 1

    @pytest.mark.asyncio
    async def test_compare_periods_tool(self, sample_dayfile_rows):
        """Verifica la tool compare_periods comparando dos periodos."""
        with patch.object(server, "_all_dayfile", new_callable=AsyncMock) as mock_all:
            mock_all.return_value = sample_dayfile_rows

            raw_res = await server.compare_periods(
                start_a="2024-01-01",
                end_a="2024-01-02",
                start_b="2024-01-03",
                end_b="2024-01-04",
            )
            res = json.loads(raw_res)

            assert "A" in res and "B" in res
            assert res["A"]["dias"] == 2
            assert res["B"]["dias"] == 2
            assert res["A"]["temp_min_absoluta"]["valor"] == -5.0
            assert res["B"]["temp_min_absoluta"]["valor"] == -2.0


# =====================================================================
# 11. Helper HTTP con Basic Auth y Control de Escritura (ENABLE_WRITE_TOOLS)
# =====================================================================

class TestHttpHelperAndWriteControl:
    @pytest.mark.asyncio
    async def test_api_request_basic_auth_enabled(self, monkeypatch):
        monkeypatch.setenv("CUMULUS_USER", "admin")
        monkeypatch.setenv("CUMULUS_PASSWORD", "secret123")

        captured_auth = []

        async def fake_request(method, url, **kwargs):
            captured_auth.append(kwargs.get("auth"))
            return httpx.Response(200, json={"ok": True})

        with patch.object(server._cli(), "request", side_effect=fake_request):
            await server._api_request("GET", "/api/test")
            assert len(captured_auth) == 1
            assert captured_auth[0] == ("admin", "secret123")

    @pytest.mark.asyncio
    async def test_api_request_basic_auth_disabled(self, monkeypatch):
        monkeypatch.delenv("CUMULUS_USER", raising=False)
        monkeypatch.delenv("CUMULUS_PASSWORD", raising=False)

        captured_auth = []

        async def fake_request(method, url, **kwargs):
            captured_auth.append(kwargs.get("auth"))
            return httpx.Response(200, json={"ok": True})

        with patch.object(server._cli(), "request", side_effect=fake_request):
            await server._api_request("GET", "/api/test")
            assert len(captured_auth) == 1
            assert captured_auth[0] is None

    @pytest.mark.asyncio
    async def test_write_tools_disabled_when_flag_false(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "false")

        # Test various write tools are blocked
        res_reload = await server.reloaddayfile()
        assert res_reload == server.WRITE_DISABLED_MSG

        res_alarms = await server.clear_alarms("all")
        assert res_alarms == server.WRITE_DISABLED_MSG

        res_clear_err = await server.clear_error_log()
        assert res_clear_err == server.WRITE_DISABLED_MSG

        res_ftp = await server.trigger_ftp()
        assert res_ftp == server.WRITE_DISABLED_MSG

        res_purge = await server.purgemysql()
        assert res_purge == server.WRITE_DISABLED_MSG

        res_gen = await server.generate_reports("month")
        assert res_gen == server.WRITE_DISABLED_MSG

        res_upload = await server.upload_reports("month")
        assert res_upload == server.WRITE_DISABLED_MSG

        res_edit_cond = await server.edit_current_conditions("Soleado")
        assert res_edit_cond == server.WRITE_DISABLED_MSG

        res_edit_rain = await server.edit_rain_today(10.5)
        assert res_edit_rain == server.WRITE_DISABLED_MSG

        res_edit_diary = await server.edit_diary("2024-05-01", "Día despejado")
        assert res_edit_diary == server.WRITE_DISABLED_MSG

        res_del_diary = await server.delete_diary("2024-05-01")
        assert res_del_diary == server.WRITE_DISABLED_MSG

        res_set_station = await server.update_station_settings({"test": 1})
        assert res_set_station == server.WRITE_DISABLED_MSG

        res_set_calib = await server.update_calibration_settings({"test": 1})
        assert res_set_calib == server.WRITE_DISABLED_MSG

        res_set_alarm = await server.update_alarm_settings({"test": 1})
        assert res_set_alarm == server.WRITE_DISABLED_MSG

        res_test_email = await server.test_email("a@b.com", "c@d.com")
        assert res_test_email == server.WRITE_DISABLED_MSG

    @pytest.mark.asyncio
    async def test_read_tools_allowed_when_write_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "false")

        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = '{"ok": true}'
            res_err = await server.get_error_log()
            assert res_err == '{"ok": true}'

            res_st = await server.get_station_settings()
            assert res_st == '{"ok": true}'

            res_cal = await server.get_calibration_settings()
            assert res_cal == '{"ok": true}'

            res_noaa = await server.get_noaa_settings()
            assert res_noaa == '{"ok": true}'


# =====================================================================
# 12. Utilidades (/utils/*)
# =====================================================================

class TestUtilsTools:
    @pytest.mark.asyncio
    async def test_reloaddayfile_clears_cache_and_calls_api(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        server._cached_dayfile_by_year = {2024: [{"fake": 1}]}
        server._dayfile_cache_time = 12345.0

        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = "OK Dayfile Reloaded"
            res = await server.reloaddayfile()

            assert "Caché local de dayfile invalidada" in res
            assert "OK Dayfile Reloaded" in res
            assert len(server._cached_dayfile_by_year) == 0
            assert server._dayfile_cache_time == 0.0
            mock_get.assert_called_once_with("/api/utils/reloaddayfile")

    @pytest.mark.asyncio
    async def test_clear_alarms_specific(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = "Reset"
            res = await server.clear_alarms("BatteryLow")
            assert res == "Reset"
            mock_post.assert_called_once_with(
                "/api/utils/clearalarm.txt",
                content=b"AlarmBatteryLow",
                headers={"Content-Type": "text/plain"},
            )

    @pytest.mark.asyncio
    async def test_clear_alarms_all(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = "Reset"
            res = await server.clear_alarms("all")
            assert "Alarmas restablecidas:" in res
            assert mock_post.call_count == len(server.ALL_ALARMS)

    @pytest.mark.asyncio
    async def test_clear_error_log(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = "[]"
            res = await server.clear_error_log()
            assert res == "[]"
            mock_post.assert_called_once_with("/api/utils/clearerrorlog.json")

    @pytest.mark.asyncio
    async def test_trigger_ftp(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = "Upload process invoked"
            res = await server.trigger_ftp(dailygraphs=True, noaa=True)
            assert res == "Upload process invoked"
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["headers"] == {"Content-Type": "application/json"}
            payload = json.loads(call_kwargs["content"])
            assert payload["dailygraphs"] is True
            assert payload["noaa"] is True
            assert payload["graphs"] is False

    @pytest.mark.asyncio
    async def test_purgemysql(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = "The MySQL cache is already empty!"
            res = await server.purgemysql()
            assert res == "The MySQL cache is already empty!"
            mock_get.assert_called_once_with("/api/utils/purgemysql")


# =====================================================================
# 13. Informes y Diagnóstico (/genreports/*, /uploadreport/*, /data/errorlog.json)
# =====================================================================

class TestReportsAndDiagnostics:
    @pytest.mark.asyncio
    async def test_generate_reports_month_and_year(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = "Report generated"

            res_m = await server.generate_reports("month", year=2024, month=5)
            assert res_m == "Report generated"
            mock_get.assert_called_with("/api/genreports/noaamonth?year=2024&month=5")

            res_y = await server.generate_reports("year", year=2024)
            assert res_y == "Report generated"
            mock_get.assert_called_with("/api/genreports/noaayear?year=2024")

    @pytest.mark.asyncio
    async def test_generate_reports_invalid_parameters(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with pytest.raises(ValueError, match="year fuera de rango"):
            await server.generate_reports("year", year=1990)

        with pytest.raises(ValueError, match="month fuera de rango"):
            await server.generate_reports("month", year=2024, month=13)

    @pytest.mark.asyncio
    async def test_upload_reports(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = "Report uploaded"
            res = await server.upload_reports("month", year=2024, month=6)
            assert res == "Report uploaded"
            mock_get.assert_called_once_with("/api/uploadreport/noaamonth?year=2024&month=6")

    @pytest.mark.asyncio
    async def test_get_error_log(self):
        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = '{"errors": []}'
            res = await server.get_error_log()
            assert res == '{"errors": []}'
            mock_get.assert_called_once_with("/api/data/errorlog.json")


# =====================================================================
# 14. Edición (/edit/*)
# =====================================================================

class TestEditTools:
    @pytest.mark.asyncio
    async def test_edit_current_conditions(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = '{"result":"Success"}'
            res = await server.edit_current_conditions("Nublado con viento fuerte")
            assert res == '{"result":"Success"}'
            mock_post.assert_called_once_with(
                "/api/edit/currcond",
                content="Nublado con viento fuerte".encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )

    @pytest.mark.asyncio
    async def test_edit_rain_today_with_midnight(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = '{"result":"Success"}'
            res = await server.edit_rain_today(12.4, 5.2)
            assert res == '{"result":"Success"}'
            mock_post.assert_called_once_with(
                "/api/edit/raintoday",
                data={"raintoday": "12.400", "midnightrain": "5.200"},
            )

    @pytest.mark.asyncio
    async def test_edit_rain_today_fetches_midnight_if_omitted(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_json", new_callable=AsyncMock) as mock_json, \
             patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_json.return_value = {"midnightrain": "3.10"}
            mock_post.return_value = '{"result":"Success"}'

            res = await server.edit_rain_today(8.0)
            assert res == '{"result":"Success"}'
            mock_json.assert_once_with("/api/edit/raintodayeditdata.json") if hasattr(mock_json, "assert_once_with") else mock_json.assert_called_once_with("/api/edit/raintodayeditdata.json")
            mock_post.assert_called_once_with(
                "/api/edit/raintoday",
                data={"raintoday": "8.000", "midnightrain": "3.100"},
            )

    @pytest.mark.asyncio
    async def test_edit_diary(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = '{"result":"Success"}'
            res = await server.edit_diary("2024-05-10", entry="Nevada copiosa", snow_depth=15.0, thunder=True)
            assert res == '{"result":"Success"}'
            call_kwargs = mock_post.call_args[1]
            payload = json.loads(call_kwargs["content"])
            assert payload["Date"] == "2024-05-10"
            assert payload["Entry"] == "Nevada copiosa"
            assert payload["SnowDepth"] == 15.0
            assert payload["Thunder"] is True

    @pytest.mark.asyncio
    async def test_delete_diary(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = '{"result":"Success"}'
            res = await server.delete_diary("2024-05-10")
            assert res == '{"result":"Success"}'
            call_kwargs = mock_post.call_args[1]
            payload = json.loads(call_kwargs["content"])
            assert payload["Date"] == "2024-05-10"


# =====================================================================
# 15. Configuración (/settings/* y /setsettings/*)
# =====================================================================

class TestSettingsTools:
    @pytest.mark.asyncio
    async def test_get_station_settings(self):
        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = '{"station": "Davis"}'
            res = await server.get_station_settings()
            assert res == '{"station": "Davis"}'
            mock_get.assert_called_once_with("/api/settings/stationdata.json")

    @pytest.mark.asyncio
    async def test_update_station_settings_merges_and_encodes(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_json", new_callable=AsyncMock) as mock_json, \
             patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_json.return_value = {"AnnualRainfall": {"ytdamount": 50.0, "rainseasonstart": 1}}
            mock_post.return_value = "success"

            res = await server.update_station_settings({"AnnualRainfall": {"ytdamount": 75.0}})
            assert res == "success"
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["content"].startswith("json=")
            decoded = json.loads(server.urllib.parse.unquote(call_kwargs["content"][5:]))
            assert decoded["AnnualRainfall"]["ytdamount"] == 75.0
            assert decoded["AnnualRainfall"]["rainseasonstart"] == 1

    @pytest.mark.asyncio
    async def test_get_alarm_settings_fallback_on_404(self):
        with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [
                "ERROR HTTP 404 en /api/settings/alarms.json",
                '{"alarm": "ok"}',
            ]
            res = await server.get_alarm_settings()
            assert res == '{"alarm": "ok"}'
            assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_update_alarm_settings(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_json", new_callable=AsyncMock) as mock_json, \
             patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_json.return_value = {"data": {"tempBelow": {"enabled": False}}}
            mock_post.return_value = "success"

            res = await server.update_alarm_settings({"tempBelow": {"enabled": True}})
            assert res == "success"
            call_kwargs = mock_post.call_args[1]
            payload = json.loads(call_kwargs["content"])
            assert payload["data"]["tempBelow"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_test_email(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
        with patch.object(server, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = "Test email sent without error"
            res = await server.test_email("station@example.com", "admin@example.com", use_html=True)
            assert res == "Test email sent without error"
            call_kwargs = mock_post.call_args[1]
            payload = json.loads(call_kwargs["content"])
            assert payload["fromEmail"] == "station@example.com"
            assert payload["destEmail"] == "admin@example.com"
            assert payload["useHtml"] is True
            assert payload["useBcc"] is False


# =====================================================================
# 16. Verificación de registro de todas las nuevas herramientas FastMCP
# =====================================================================

class TestToolRegistration:
    @pytest.mark.asyncio
    async def test_all_new_tools_registered(self):
        tools = await server.mcp.list_tools()
        registered_names = {t.name for t in tools}
        expected_new_tools = {
            "reloaddayfile",
            "clear_alarms",
            "clear_error_log",
            "trigger_ftp",
            "purgemysql",
            "generate_reports",
            "upload_reports",
            "get_error_log",
            "edit_current_conditions",
            "edit_rain_today",
            "edit_diary",
            "delete_diary",
            "get_station_settings",
            "update_station_settings",
            "get_calibration_settings",
            "update_calibration_settings",
            "get_alarm_settings",
            "update_alarm_settings",
            "get_noaa_settings",
            "test_email",
        }
        missing = expected_new_tools - registered_names
        assert not missing, f"Herramientas no registradas en FastMCP: {missing}"

    @pytest.mark.asyncio
    async def test_tool_annotations_for_claude_permissions(self):
        """Verifica que todas las herramientas tienen anotaciones de solo lectura o destructivas."""
        tools = await server.mcp.list_tools()
        assert len(tools) == 47
        read_count = 0
        write_count = 0
        for t in tools:
            assert hasattr(t, "annotations") and t.annotations is not None, f"Herramienta {t.name} sin anotaciones"
            assert t.annotations.read_only_hint in (True, False), f"Herramienta {t.name} sin read_only_hint definido"
            if t.annotations.read_only_hint:
                read_count += 1
                assert t.annotations.destructive_hint is False
            else:
                write_count += 1
                assert t.annotations.destructive_hint is True
        assert read_count == 32
        assert write_count == 15


class TestCheckCumulusConnection:
    """Pruebas para la comprobación inicial de conectividad con CumulusMX."""

    def test_check_cumulus_connection_success(self):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"Version": "5.1.6", "Build": "5016"}
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value.__enter__.return_value = mock_client

            ok, msg = server.check_cumulus_connection()
            assert ok is True
            assert "5.1.6" in msg
            assert "5016" in msg

    def test_check_cumulus_connection_auth_error(self):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value.__enter__.return_value = mock_client

            ok, msg = server.check_cumulus_connection()
            assert ok is False
            assert "401" in msg

    def test_check_cumulus_connection_network_error(self):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client_cls.return_value.__enter__.return_value = mock_client

            ok, msg = server.check_cumulus_connection()
            assert ok is False
            assert "Connection refused" in msg



