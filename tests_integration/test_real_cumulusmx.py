"""Cada herramienta del MCP, ejecutada contra un CumulusMX real (ver conftest.py).

1. `test_every_tool_is_accounted_for`: ninguna herramienta registrada puede quedar sin probar; si no se puede probar
   contra el Simulator (hardware, MySQL, FTP, correo) debe figurar en EXCLUDED con el motivo.
2. Herramientas de lectura: se ejecutan todas con argumentos válidos y deben devolver datos, no errores.
3. Herramientas de escritura: editar -> comprobar -> restaurar con la copia, comparando los FICHEROS REALES del directorio
   de datos de CumulusMX (dayfile, logs, ini, SQLite) para demostrar que los datos quedan como estaban.
"""
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastmcp import Client

import server  # noqa: E402  (fachada instalada por conftest)

pytestmark = pytest.mark.asyncio

# --------------------------------------------------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------------------------------------------------


def text_of(result) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def failed(result) -> bool:
    flag = getattr(result, "is_error", None)
    return bool(getattr(result, "isError", False) if flag is None else flag)


async def call(mcp_client: Client, tool: str, /, **args) -> str:
    """Llama a una herramienta; si el servidor MCP la rechaza lanza AssertionError con el motivo."""
    result = await mcp_client.call_tool(tool, args, raise_on_error=False)
    text = text_of(result)
    assert not failed(result), f"{tool}({args}) -> error MCP: {text[:600]}"
    return text


async def call_json(mcp_client: Client, tool: str, /, **args):
    text = await call(mcp_client, tool, **args)
    try:
        return json.loads(text)
    except ValueError:
        pytest.fail(f"{tool}({args}) no devolvió JSON: {text[:400]}")


def blank_as_none(node):
    """CumulusMX guarda un texto vacío o un espacio como null al guardar ajustes: para comparar son lo mismo."""
    if isinstance(node, dict):
        return {k: blank_as_none(v) for k, v in node.items()}
    if isinstance(node, list):
        return [blank_as_none(v) for v in node]
    return None if isinstance(node, str) and not node.strip() else node


def assert_ok_text(name: str, args: dict, text: str):
    assert text.strip(), f"{name}({args}) devolvió texto vacío"
    assert not text.startswith("ERROR"), f"{name}({args}) -> {text[:600]}"


@pytest.fixture
async def client():
    from cumulusmx_mcp import client as cumulus_client

    cumulus_client._client = None            # cada test tiene su propio bucle de eventos: el cliente HTTP no se comparte
    try:
        async with Client(server.mcp) as c:
            yield c
    finally:
        http = cumulus_client._client
        cumulus_client._client = None
        if http is not None:
            await http.aclose()


def d(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


# --------------------------------------------------------------------------------------------------------------------
# 1. Cobertura obligatoria
# --------------------------------------------------------------------------------------------------------------------

SETTINGS_SECTIONS = ["program", "station", "internet", "thirdparty", "extrasensors", "extrawebfiles", "calibration", "language",
                     "noaa", "mysql", "alarms", "useralarms", "wizard", "customlogs_interval", "customlogs_daily", "display",
                     "httpfiles", "mqtt"]


def read_calls(s: dict) -> dict[str, dict]:
    """Argumentos válidos de cada herramienta de LECTURA, según las fechas de los datos sembrados."""
    last, first = d(s["dayfile_last"]), d(s["dayfile_first"])
    month_ago = last - dt.timedelta(days=29)
    log_day = s["log_first"][:10]
    log_next = (d(log_day) + dt.timedelta(days=1)).isoformat()
    return {
        "anomaly": dict(start=month_ago.isoformat(), end=last.isoformat()),
        "api_get": dict(path="/api/info/version.json"),
        "climate_summary": dict(start=first.isoformat(), end=last.isoformat()),
        "climatology": {},
        "compare_periods": dict(start_a=(last - dt.timedelta(days=59)).isoformat(), end_a=(last - dt.timedelta(days=30)).isoformat(),
                                start_b=month_ago.isoformat(), end_b=last.isoformat()),
        "count_days": dict(variable="temp_max", op=">=", value=20),
        "current_data": {},
        "daily_briefing": {},
        "daily_data": dict(start=first.isoformat(), end=(first + dt.timedelta(days=5)).isoformat(),
                           fields=["MaxTemperature", "MinTemperature"]),
        "daily_graph_data": dict(name="tempdata"),
        "data_quality": {},
        "dayfile": dict(start=0, length=5),
        "dayfile_range": dict(start=month_ago.isoformat(), end=last.isoformat()),
        "degree_days": dict(start=month_ago.isoformat(), end=last.isoformat()),
        "diary": {},
        "diary_export": {},
        "extra_sensors": dict(sensor="temp"),
        "find_streaks": dict(variable="temp_min", op="<=", value=0),
        "frost_dates": {},
        "get_alarm_settings": {},
        "get_audit_log": {},
        "get_calibration_settings": {},
        "get_current_conditions_text": {},
        "get_error_log": {},
        "get_mqtt_settings": {},
        "get_mysql_settings": {},
        "get_noaa_settings": {},
        "get_station_settings": {},
        "get_tags": dict(tags=["temp", "hum", "press"]),
        "graph_data": dict(name="availabledata"),
        "graph_summary": dict(name="tempdata"),
        "interval_data": dict(start=f"{log_day} 00:00", end=f"{log_day} 12:00", fields=["Temperature", "Humidity"]),
        "list_backups": {},
        "live_sample": dict(seconds=4, max_messages=2),
        "logfile": dict(start=log_day, end=log_next, length=5),
        "monthly_summary": dict(year=last.year),
        "mysql_cache": {},
        "noaa_report": dict(period="noaamonth", year=last.year, month=last.month),
        "period_report": dict(period="month", ref_date=last.isoformat()),
        "ping": {},
        "rankings": dict(variable="temp_max", top=3),
        "recent_data": dict(length=5),
        "records": dict(scope="alltime", kind="temperature"),
        "records_all": dict(scope="alltime"),
        "records_edit_data": dict(scope="alltime"),
        "records_period": dict(startdate=month_ago.isoformat(), enddate=last.isoformat()),
        "records_query": dict(dataname="HighTemp", function="max", period="User", start=month_ago.isoformat(), end=last.isoformat()),
        "render_template": dict(template="<#temp> <#hum>"),
        "server_status": {},
        "simple_report": dict(report="temperature"),
        "snapshot": {},
        "station_info": dict(kind="version"),
        "today_all": {},
        "today_yesterday": dict(kind="temp"),
        "weather_alerts": {},
        "wind_rose": dict(start=month_ago.isoformat(), end=last.isoformat()),
        "year_records": dict(year=last.year),
    }


# Lecturas cuyos argumentos dependen de lo que CumulusMX publique en ese momento (nombre del test que las cubre)
READ_DYNAMIC = {"graph_select": "test_graph_select_with_real_series_names"}

# Herramientas de escritura probadas en los escenarios de este fichero (nombre del test que las cubre)
WRITE_COVERED = {
    "edit_dayfile": "test_dayfile_edit_and_restore_leaves_the_real_file_identical",
    "edit_datalog": "test_datalog_edit_and_restore_leaves_the_real_file_identical",
    "edit_recent_data": "test_recent_data_edit_and_restore",
    "edit_records": "test_records_edit_and_restore",
    "restore_backup": "(todos los escenarios de edición)",
    "get_backup": "test_dayfile_edit_and_restore_leaves_the_real_file_identical",
    "update_settings": "test_every_settings_section_survives_update_and_restore",
    "update_mysql_settings": "test_dedicated_settings_tools",
    "update_mqtt_settings": "test_dedicated_settings_tools",
    "update_noaa_settings": "test_dedicated_settings_tools",
    "update_station_settings": "test_classic_settings_updaters",
    "update_calibration_settings": "test_classic_settings_updaters",
    "update_alarm_settings": "test_classic_settings_updaters",
    "edit_diary": "test_diary_roundtrip",
    "delete_diary": "test_diary_roundtrip",
    "diary_upload": "test_diary_roundtrip",
    "diary_automate": "test_diary_automate_off",
    "edit_current_conditions": "test_current_conditions_and_rain_today",
    "edit_rain_today": "test_current_conditions_and_rain_today",
    "reloaddayfile": "test_maintenance_tools",
    "clear_alarms": "test_maintenance_tools",
    "clear_error_log": "test_maintenance_tools",
    "generate_reports": "test_generate_reports",
    "station_input": "test_station_input_reports_a_clean_error",
}

# Lo que NO puede probarse contra el Simulator, con el motivo. Se llaman igualmente: deben fallar con un mensaje, no con una excepción.
EXCLUDED = {
    "edit_mysql_cache": "necesita comandos pendientes en la caché de MySQL (no hay MySQL); cubierto por el simulador de tests/",
    "mysql_table": "necesita un servidor MySQL real; cubierto por el simulador de tests/",
    "purgemysql": "necesita un servidor MySQL real",
    "test_email": "necesita un servidor SMTP real",
    "trigger_ftp": "necesita un servidor FTP real",
    "upload_reports": "necesita un servidor FTP real",
}
GRACEFUL_CALLS = {
    "edit_mysql_cache": dict(action="Delete", keys=[1]),
    "mysql_table": dict(action="create", table="monthly"),
    "purgemysql": {},
    "test_email": dict(from_email="a@example.com", dest_email="b@example.com"),
    "trigger_ftp": {},
    "upload_reports": dict(report_type="month"),
}


async def test_every_tool_is_accounted_for(client, seed):
    registered = {t.name for t in await client.list_tools()}
    reads = set(read_calls(seed)) | {"get_settings"} | set(READ_DYNAMIC)
    writes, excluded = set(WRITE_COVERED), set(EXCLUDED)
    known = reads | writes | excluded
    assert not (registered - known), f"herramientas SIN prueba contra CumulusMX real: {sorted(registered - known)}"
    assert not (known - registered), f"la tabla menciona herramientas que ya no existen: {sorted(known - registered)}"
    assert not (reads & writes) and not (excluded & (reads | writes)), "una herramienta figura en dos categorías"
    assert set(GRACEFUL_CALLS) == excluded


# --------------------------------------------------------------------------------------------------------------------
# 2. Lectura
# --------------------------------------------------------------------------------------------------------------------

READ_NAMES = sorted(read_calls({"dayfile_last": "2026-01-01", "dayfile_first": "2025-01-01", "log_first": "2025-01-01T00:00:00"}))


@pytest.mark.parametrize("tool", READ_NAMES)
async def test_read_tool_returns_real_data(client, seed, tool):
    args = read_calls(seed)[tool]
    text = await call(client, tool, **args)
    assert_ok_text(tool, args, text)
    if text.lstrip().startswith(("{", "[")):
        json.loads(text)                                  # si dice ser JSON, tiene que serlo


@pytest.mark.parametrize("section", SETTINGS_SECTIONS)
async def test_get_settings_every_section(client, section):
    text = await call(client, "get_settings", section=section)
    assert_ok_text("get_settings", {"section": section}, text)
    json.loads(text)


async def test_graph_select_with_real_series_names(client, seed):
    available = await call_json(client, "graph_data", name="availabledata")
    names: list[str] = []

    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                names.append(str(key))
                collect(value)
        elif isinstance(node, list):
            for value in node:
                names.append(value) if isinstance(value, str) else collect(value)

    collect(available)
    assert names, f"availabledata sin series: {available}"
    last = d(seed["dayfile_last"])
    start = (last - dt.timedelta(days=5)).isoformat()
    tried = []
    for name in names[:6]:
        out = await call(client, "graph_select", kind="chart", series=[name], from_date=start, to_date=last.isoformat())
        if out.strip() and not out.startswith("ERROR"):
            return
        tried.append((name, out[:120]))
    pytest.fail(f"graph_select no devolvió datos con ninguna serie probada: {tried}")


async def test_history_tools_agree_with_the_seeded_data(client, seed):
    """Los números salen del dayfile sembrado: lo comprueba el test, no el MCP."""
    first, last = seed["dayfile_first"], seed["dayfile_last"]
    summary = await call_json(client, "dayfile_range", start=first, end=last, summary_only=True)
    n_days = summary.get("n_dias") or summary.get("n_days")
    assert n_days == seed["dayfile_lines"], summary
    quality = await call(client, "data_quality")
    for missing in seed["gap"]:
        assert missing in quality, f"data_quality no detectó el día sin datos {missing}: {quality[:500]}"
    frost = await call_json(client, "count_days", variable="temp_min", op="<=", value=0)
    assert frost, frost
    top = await call_json(client, "rankings", variable="temp_max", top=1)
    assert top, top


async def test_live_sample_receives_messages(client):
    out = await call_json(client, "live_sample", seconds=5, max_messages=2)
    assert not out.get("error"), out
    assert out.get("mensajes") or out.get("messages"), out


# --------------------------------------------------------------------------------------------------------------------
# 3. Escritura: editar -> comprobar -> restaurar, con los ficheros reales
# --------------------------------------------------------------------------------------------------------------------


def envelope(text: str) -> dict:
    data = json.loads(text)
    assert isinstance(data, dict) and "copia_id" in data, text[:400]
    return data


async def restore(client, backup_id: str) -> dict:
    preview = json.loads(await call(client, "restore_backup", backup_id=backup_id))
    assert preview.get("aplicado") is False, preview
    return json.loads(await call(client, "restore_backup", backup_id=backup_id, dry_run=False))


async def test_dayfile_edit_and_restore_leaves_the_real_file_identical(client, data_dir):
    path = data_dir / "dayfile.txt"
    before = path.read_bytes()
    rows = (await call_json(client, "dayfile", start=4, length=1))["data"]
    line, values = int(rows[0][0]), [str(v) for v in rows[0][1:]]
    new = list(values)
    new[6] = "88.8"                                               # temperatura máxima (campo 6 de la línea; el 5 es una hora)
    res = envelope(await call(client, "edit_dayfile", action="Edit", line=line, values=new))
    assert "ERROR" not in res["respuesta"] and res["copia_id"], res
    changed = path.read_bytes()
    assert changed != before and b"88.8" in changed, "CumulusMX no cambió el dayfile"
    backup = json.loads(await call(client, "get_backup", backup_id=res["copia_id"]))
    assert backup["tipo"] == "dayfile"
    out = await restore(client, res["copia_id"])
    assert out.get("aplicado") is True, out
    assert path.read_bytes() == before, "el dayfile restaurado NO es idéntico al original"


async def test_datalog_edit_and_restore_leaves_the_real_file_identical(client, seed, data_dir):
    path = data_dir / seed["log_file"]
    before = path.read_bytes()
    rows = (await call_json(client, "logfile", start=seed["log_first"][:10], end=seed["log_first"][:10], offset=3, length=1))["data"]
    line, values = int(rows[0][0]), [str(v) for v in rows[0][1:]]
    new = list(values)
    new[1 + 1] = "55.5"                                           # temperatura (tras fecha y marca de tiempo)
    res = envelope(await call(client, "edit_datalog", action="Edit", line=line, values=new))
    assert "ERROR" not in res["respuesta"] and res["copia_id"], res
    assert b"55.5" in path.read_bytes() and path.read_bytes() != before
    out = await restore(client, res["copia_id"])
    assert out.get("aplicado") is True, out
    assert path.read_bytes() == before, "el log restaurado NO es idéntico al original"


def recent_db_row(data_dir: Path, timestamp: int):
    con = sqlite3.connect(f"file:{data_dir / 'cumulusmx.db'}?mode=ro", uri=True)
    try:
        return con.execute("select * from RecentData where Timestamp = ?", (timestamp,)).fetchone()
    finally:
        con.close()


async def test_recent_data_edit_and_restore(client, data_dir):
    rows = (await call_json(client, "recent_data", offset=1, length=1))["data"]
    assert rows, "la tabla de datos recientes sigue vacía"
    original = rows[0]
    timestamp = int(original[1])
    db_before = recent_db_row(data_dir, timestamp)
    new = list(original)
    new[7] = round(float(original[7]) + 1.0, 1)                   # temperatura exterior
    res = envelope(await call(client, "edit_recent_data", action="Edit", values=new))
    assert "ERROR" not in res["respuesta"] and res["copia_id"], res
    edited = (await call_json(client, "recent_data", offset=1, length=1))["data"][0]
    assert edited[7] == new[7], f"CumulusMX no aplicó el cambio: {edited[7]} != {new[7]}"
    await restore(client, res["copia_id"])
    restored = (await call_json(client, "recent_data", offset=1, length=1))["data"][0]
    assert restored == original, f"el registro restaurado difiere\n{original}\n{restored}"
    db_after = recent_db_row(data_dir, timestamp)
    assert db_before is not None and db_after is not None
    for a, b in zip(db_before, db_after):
        if isinstance(a, float) or isinstance(b, float):
            assert abs(float(a) - float(b)) < 0.06, f"la base de datos cambió más que la precisión mostrada: {db_before} {db_after}"
        else:
            assert a == b, f"la base de datos cambió: {db_before} {db_after}"


def ini_without_seconds(path: Path) -> str:
    """Limitación de CumulusMX, no del MCP: los .ini guardan fechas ISO con segundos y valores con todos sus decimales, pero el
    editor de récords solo acepta minutos y el valor tal como se MUESTRA. Se compara hasta esa precisión (1 decimal)."""
    text = re.sub(r"(\d{4}-\d\d-\d\dT\d\d:\d\d):\d\d", r"\1:00", path.read_text(encoding="utf-8"))
    return re.sub(r"(=-?\d+\.\d)\d+", r"\1", text)


RECORD_INI = {"alltime": "alltime.ini", "monthly": "monthlyalltime.ini", "thisyear": "year.ini", "thismonth": "month.ini"}


def record_lines(data_dir: Path, scope: str, field: str, month: int | None = None) -> list[str]:
    """Líneas '<campo>value=' y '<campo>time=' del .ini (solo las del campo y mes editados: el Simulator sigue actualizando
    los demás récords en vivo, así que comparar el fichero entero sería comparar contra una estación que no se queda quieta)."""
    key = re.compile(rf"^{field.lower()}(value|time)=", re.I)
    out, section = [], ""
    for line in ini_without_seconds(data_dir / RECORD_INI[scope]).splitlines():
        if line.startswith("["):
            section = line
        elif key.match(line) and (month is None or section.endswith(f"{month:02d}]")):
            out.append(line)
    return out


# Campos que el Simulator NO modifica por sí solo en pocos minutos (los de temperatura, viento o humedad sí)
@pytest.mark.parametrize("scope,field,value,time", [
    ("alltime", "longestWetPeriod", 5, "03/01/2026"),
    ("alltime", "longestDryPeriod", 12, "04/01/2026"),
    ("alltime", "highHourlyRain", 1.5, "01/01/2026 12:00"),
    ("thisyear", "longestDryPeriod", 9, "05/05/2026"),
])
async def test_records_edit_and_restore(client, data_dir, scope, field, value, time):
    api_before = json.loads(await call(client, "records_edit_data", scope=scope))
    lines_before = record_lines(data_dir, scope, field)
    if scope != "thisyear":
        assert lines_before, f"no encontré {field} en el .ini"
    res = envelope(await call(client, "edit_records", scope=scope, field=field, value=value, time=time))
    assert "ERROR" not in res["respuesta"] and res["copia_id"], res
    shown = float(json.loads(await call(client, "records_edit_data", scope=scope))[f"{field}Val"].replace(",", "."))
    assert abs(shown - float(value)) <= 0.51, f"CumulusMX muestra {shown}, se esperaba {value}"
    out = await restore(client, res["copia_id"])
    assert out.get("aplicado") is True, out
    api_after = json.loads(await call(client, "records_edit_data", scope=scope))
    assert (api_after[f"{field}Val"], api_after[f"{field}Time"]) == (api_before[f"{field}Val"], api_before[f"{field}Time"]), \
        "el récord restaurado difiere del original"
    assert record_lines(data_dir, scope, field) == lines_before, "el .ini difiere tras restaurar (hasta la precisión que muestra CumulusMX)"


async def test_monthly_record_edit_chain_and_restore(client, data_dir):
    """Un récord mensual vacío no puede restaurarse a 'vacío' (el editor no lo permite): se avisa; el siguiente sí."""
    first = envelope(await call(client, "edit_records", scope="monthly", field="highDailyRain", value=11.5, time="04/07/2026", month=7))
    lines_after_first = record_lines(data_dir, "monthly", "highDailyRain", 7)
    second = envelope(await call(client, "edit_records", scope="monthly", field="highDailyRain", value=22.5, time="05/07/2026", month=7))
    out = await restore(client, second["copia_id"])
    assert out.get("aplicado") is True, out
    assert record_lines(data_dir, "monthly", "highDailyRain", 7) == lines_after_first, "no volvió al estado tras la primera edición"
    previous = json.loads(await call(client, "get_backup", backup_id=first["copia_id"]))["contenido"]
    attempt = json.loads(await call(client, "restore_backup", backup_id=first["copia_id"], dry_run=False))
    if str(previous.get("valor", "")).strip() in ("-", ""):
        assert attempt.get("aplicado") is False and "no tenía valor" in attempt.get("error", ""), attempt
    else:
        assert attempt.get("aplicado") is True, attempt


def perturb(node, path=()):
    """Primer texto 'seguro' del JSON (no contraseñas ni credenciales) -> (ruta, valor nuevo)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if (isinstance(value, str) and value and re.search(r"name|title|desc|comment|host|server|prefix|suffix|label|city|site|subject", key, re.I)
                    and not re.search(r"pass|secret|token|key|user|email|url|path|file|addr|date|time", key, re.I)):
                return path + (key,), value + "-ci"
            found = perturb(value, path + (key,))
            if found:
                return found
    return None


def nested(path, value) -> dict:
    for key in reversed(path):
        value = {key: value}
    return value


def at(node, path):
    for key in path:
        node = node[key]
    return node


async def test_every_settings_section_survives_update_and_restore(client):
    exercised = []
    for section in SETTINGS_SECTIONS:
        before = json.loads(await call(client, "get_settings", section=section))
        found = perturb(before)
        if not found:
            continue
        path, new = found
        res = envelope(await call(client, "update_settings", section=section, settings=nested(path, new), dry_run=False))
        assert res.get("aplicado") is True, (section, res)
        during = json.loads(await call(client, "get_settings", section=section))
        assert at(during, path) == new, f"{section}: CumulusMX no guardó el cambio en {path}"
        out = await restore(client, res["copia_id"])
        assert out.get("aplicado") is True, (section, out)
        after = json.loads(await call(client, "get_settings", section=section))
        assert blank_as_none(after) == blank_as_none(before), f"{section}: la sección restaurada difiere de la original"
        exercised.append(section)
    assert len(exercised) >= 8, f"demasiadas secciones sin probar: solo {exercised}"


async def test_dedicated_settings_tools(client):
    for tool, section, change in (
        ("update_mysql_settings", "mysql", {"server": {"host": "db.ci.example"}}),
        ("update_mqtt_settings", "mqtt", {"server": "mqtt.ci.example"}),
    ):
        before = json.loads(await call(client, "get_settings", section=section))
        preview = json.loads(await call(client, tool, settings=change, dry_run=True))
        assert preview.get("aplicado") is False and preview.get("campos_modificados"), preview
        assert json.loads(await call(client, "get_settings", section=section)) == before, "dry_run modificó la configuración"
        res = envelope(await call(client, tool, settings=change, dry_run=False))
        assert res.get("aplicado") is True, res
        await restore(client, res["copia_id"])
        assert blank_as_none(json.loads(await call(client, "get_settings", section=section))) == blank_as_none(before), f"{section} no volvió a su estado"
    noaa_before = json.loads(await call(client, "get_settings", section="noaa"))
    found = perturb(noaa_before)
    assert found, "la sección noaa no tiene ningún texto que probar"
    res = envelope(await call(client, "update_noaa_settings", settings=nested(*found[:1], found[1]), dry_run=False))
    await restore(client, res["copia_id"])
    assert blank_as_none(json.loads(await call(client, "get_settings", section="noaa"))) == blank_as_none(noaa_before)


async def test_classic_settings_updaters(client):
    for tool, getter, section in (("update_station_settings", "get_station_settings", "station"),
                                  ("update_calibration_settings", "get_calibration_settings", "calibration"),
                                  ("update_alarm_settings", "get_alarm_settings", "alarms")):
        before = json.loads(await call(client, "get_settings", section=section))
        found = perturb(before)
        if not found:
            pytest.skip(f"{section}: sin texto que modificar")
        path, new = found
        out = await call(client, tool, settings=nested(path, new))
        assert_ok_text(tool, {}, out)
        assert at(json.loads(await call(client, "get_settings", section=section)), path) == new, f"{tool} no cambió {path}"
        backups = json.loads(await call(client, "list_backups", kind="settings"))["copias"]
        assert backups, f"{tool} no dejó copia"
        await restore(client, backups[0]["id"])
        assert blank_as_none(json.loads(await call(client, "get_settings", section=section))) == blank_as_none(before), f"{section} no volvió a su estado"
        assert_ok_text(getter, {}, await call(client, getter))


async def test_diary_roundtrip(client):
    day = "2026-10-01"
    marker = "entrada de prueba CI"
    assert_ok_text("edit_diary", {}, await call(client, "edit_diary", date=day, entry=marker, thunder=True))
    assert marker in await call(client, "diary", date=day)
    exported = await call(client, "diary_export")
    assert marker in exported
    assert_ok_text("delete_diary", {}, await call(client, "delete_diary", date=day))
    assert marker not in await call(client, "diary_export")
    assert_ok_text("diary_upload", {}, await call(client, "diary_upload", csv_content=exported))     # reimporta lo exportado
    assert marker in await call(client, "diary_export")
    await call(client, "delete_diary", date=day)                                                       # limpieza
    assert marker not in await call(client, "diary_export")


async def test_diary_automate_off(client):
    assert_ok_text("diary_automate", {}, await call(client, "diary_automate", sensor=0))


async def test_current_conditions_and_rain_today(client):
    original_text = await call(client, "get_current_conditions_text")
    assert_ok_text("edit_current_conditions", {}, await call(client, "edit_current_conditions", text="prueba de integración"))
    assert "prueba de integración" in await call(client, "get_current_conditions_text")
    await call(client, "edit_current_conditions", text=original_text)
    def rain(tags: dict) -> float:
        return float(str(tags["rfall"]).replace(",", "."))

    original_rain = rain(json.loads(await call(client, "get_tags", tags=["rfall"])))
    res = envelope(await call(client, "edit_rain_today", raintoday=1.5))
    assert "ERROR" not in res["respuesta"] and res["copia_id"], res
    assert abs(rain(json.loads(await call(client, "get_tags", tags=["rfall"]))) - 1.5) < 0.06, "CumulusMX no aplicó la lluvia de hoy"
    out = await restore(client, res["copia_id"])
    assert out.get("aplicado") is True, out
    assert abs(rain(json.loads(await call(client, "get_tags", tags=["rfall"]))) - original_rain) < 0.06, "la lluvia no volvió a su valor"


async def test_maintenance_tools(client, data_dir):
    dayfile = (data_dir / "dayfile.txt").read_bytes()
    assert_ok_text("reloaddayfile", {}, await call(client, "reloaddayfile"))
    assert (data_dir / "dayfile.txt").read_bytes() == dayfile, "reloaddayfile modificó el fichero"
    assert_ok_text("clear_alarms", {}, await call(client, "clear_alarms", alarm="all"))
    assert_ok_text("clear_error_log", {}, await call(client, "clear_error_log"))


async def test_generate_reports(client, seed):
    last = d(seed["dayfile_last"])
    assert_ok_text("generate_reports", {}, await call(client, "generate_reports", report_type="month", year=last.year, month=last.month))


async def test_station_input_reports_a_clean_error(client):
    out = await call(client, "station_input", protocol="json", method="POST", body="{}")
    assert "503" in out or "not running" in out.lower() or "ERROR" in out, out      # el Simulator no es una estación HTTP


@pytest.mark.parametrize("tool", sorted(EXCLUDED))
async def test_tools_that_need_external_services_fail_cleanly(client, tool):
    result = await client.call_tool(tool, GRACEFUL_CALLS[tool], raise_on_error=False)
    text = text_of(result)
    assert text.strip(), f"{tool}: sin respuesta"
    assert "Traceback" not in text, text[:500]


async def test_audit_log_recorded_the_writes(client):
    log = json.loads(await call(client, "get_audit_log", limit=500))
    tools = {r["herramienta"] for r in log["registros"]}
    assert {"edit_dayfile", "edit_datalog", "restore_backup"} <= tools, tools
