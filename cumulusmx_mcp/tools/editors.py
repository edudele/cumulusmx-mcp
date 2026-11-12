"""Tools de corrección de datos históricos y entrada de datos de estaciones HTTP."""
import json
import re
import urllib.parse
from typing import Any, Literal


from .. import backups, capture, client
from .. import dayrows
from .. import safety
from .. import settings_engine
from ..app import write_tool


Cell = str | int | float | None


def _cell(value: Cell) -> str:
    """Un valor de una fila de log/dayfile como texto; None (campo vacío) se envía como cadena vacía."""
    return "" if value is None else str(value)


def _editor_payload(action: str, lines: list[int] | None = None, data: list[list[str]] | None = None, **extra: Any) -> str:
    if action not in ("Edit", "Delete"):
        raise ValueError("action debe ser 'Edit' o 'Delete'")
    body: dict[str, Any] = {"action": action}
    if lines is not None:
        body["lines"] = lines
    if data is not None:
        body["data"] = data
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def _envelope(response: str, backup_id: str | None, what: str) -> str:
    """Respuesta de CumulusMX junto con el id de la copia previa (y un aviso si debía haberla y no se pudo)."""
    result: dict[str, Any] = {"respuesta": response, "copia_id": backup_id}
    if backups.enabled() and backup_id is None:
        result["aviso"] = f"No se pudo guardar la copia previa de {what} (ver el log del servidor)."
    return json.dumps(result, ensure_ascii=False)


def _check_action(action: str) -> None:
    if action not in ("Edit", "Delete"):
        raise ValueError("action debe ser 'Edit' o 'Delete'")


async def _edit_dayfile(action: str, line: int, values: list[str | int | float | None]) -> str:
    """Implementación de edit_dayfile (también la usa restore_backup)."""
    _check_action(action)
    if line < 1 or not values:
        raise ValueError("line debe ser >= 1 y values no puede estar vacío")
    payload = _editor_payload(action, [line], [[_cell(v) for v in values]])
    row = await capture.dayfile_line(line) if backups.enabled() else None
    backup_id = backups.save("dayfile", f"linea{line}", {"line": line, "row": row, "accion": action}, "edit_dayfile") if row else None
    response = await client._post("/api/edit/dayfile", content=payload, headers=settings_engine.JSON_HEADERS)
    dayrows.invalidate_cache()
    return _envelope(response, backup_id, "la línea")


@write_tool
async def edit_dayfile(action: Literal["Edit", "Delete"], line: int, values: list[str | int | float | None]) -> str:
    """Edita o borra UNA línea del dayfile.txt (corrección de datos históricos). line: número de línea (1 = primera; es el
    primer valor que devuelve la tool dayfile). values: los campos de esa línea tal como aparecen en dayfile.txt (fecha
    dd/MM/yy primero, sin el número de línea). CumulusMX comprueba que la fecha coincide con la de la línea indicada.
    Actualiza también SQLite y, si está activado 'update on edit', MySQL. Guarda antes una copia de la línea original
    (copia_id en la respuesta; una edición se deshace con restore_backup; un borrado no se puede reinsertar solo)."""
    return await _edit_dayfile(action, line, values)


async def _edit_datalog(action: str, line: int, values: list[str | int | float | None], extra: bool) -> str:
    _check_action(action)
    if line < 1 or len(values) < 2:
        raise ValueError("line debe ser >= 1 y values debe incluir al menos DateTime y TS")
    ts = str(values[1])
    payload = _editor_payload(action, [line], [[_cell(v) for v in values]], extra=extra)
    row = await capture.datalog_line(line, ts, extra) if backups.enabled() else None
    backup_id = backups.save("datalog", f"linea{line}-{ts}", {"line": line, "extra": extra, "ts": ts, "row": row,
                                                              "accion": action}, "edit_datalog") if row else None
    response = await client._post("/api/edit/datalogs", content=payload, headers=settings_engine.JSON_HEADERS)
    return _envelope(response, backup_id, "la línea del log")


@write_tool
async def edit_datalog(action: Literal["Edit", "Delete"], line: int, values: list[str | int | float | None], extra: bool = False) -> str:
    """Edita o borra una línea de un log mensual de intervalo (extra=True: log de sensores extra). El fichero se deduce del
    segundo campo (TS, segundos Unix) de values; line es la línea dentro de ese fichero (1 = primera, es el primer valor de
    las filas de la tool logfile). values: todos los campos de la línea del log (sin el número de línea). CumulusMX comprueba
    que el TS coincide con el de la línea. Guarda antes una copia de la línea original (copia_id; restaurable si fue una
    edición)."""
    return await _edit_datalog(action, line, values, extra)


RECENT_MIN_FIELDS = 25      # RecentData.FromCsvArray de CumulusMX rechaza menos campos (la fila completa lleva 27 tras la fecha)


def _recent_row(values: list) -> list[str]:
    """Campos que espera el editor de CumulusMX: empiezan por la marca de tiempo (RecentData.FromCsvArray).

    `recent_data` devuelve la fecha en texto ("05/11/26 11:00") delante de la marca de tiempo; esa fecha es solo para mostrar
    y el servidor la rechaza (long.Parse), así que se descarta si viene. Un None se envía como "null", igual que lo escribe
    CumulusMX."""
    row = ["null" if v is None else str(v) for v in values]
    if row and not row[0].strip().lstrip("-").isdigit():
        row = row[1:]
    if len(row) < RECENT_MIN_FIELDS or not row[0].strip().isdigit():
        raise ValueError("values debe ser la fila completa de recent_data (fecha opcional, marca de tiempo y al menos "
                         f"{RECENT_MIN_FIELDS - 1} valores más); no se envía nada")
    return row


async def _edit_recent(action: str, values: list[str | int | float | None]) -> str:
    _check_action(action)
    sent = _recent_row(values)
    timestamp = sent[0].strip()
    payload = _editor_payload(action, data=[sent])
    row = await capture.recent_row(timestamp) if backups.enabled() else None
    backup_id = backups.save("recent", timestamp, {"row": row, "accion": action}, "edit_recent_data") if row else None
    response = await client._post("/api/edit/recentdata", content=payload, headers=settings_engine.JSON_HEADERS)
    return _envelope(response, backup_id, "el registro")


@write_tool
async def edit_recent_data(action: Literal["Edit", "Delete"], values: list[str | int | float | None]) -> str:
    """Edita o borra un registro de la tabla de datos recientes (SQLite) de CumulusMX. values: la fila COMPLETA tal como la
    devuelve la tool recent_data (la marca de tiempo identifica el registro; la fecha en texto del principio se ignora).
    CumulusMX guarda esos valores con los decimales con que los muestra, así que un registro editado o restaurado queda con
    esa precisión. Guarda antes una copia del registro original (copia_id; restaurable si fue una edición)."""
    return await _edit_recent(action, values)


async def _edit_mysql_cache(action: str, keys: list[int], statements: list[str] | None) -> str:
    """CumulusMX: Edit usa SOLO la primera clave y sentencia; Delete recorre `keys` Y `statements` (sin statements da 500)."""
    _check_action(action)
    if not keys:
        raise ValueError("keys no puede estar vacío")
    if action == "Edit":
        if len(keys) != 1 or not statements or len(statements) != 1:
            raise ValueError("Edit actúa sobre una sola sentencia: pasa exactamente 1 clave y 1 statement")
    originals = await capture.mysql_cache_statements(keys) if (backups.enabled() or not statements) else {}
    if action == "Delete":
        if statements is None:                       # CumulusMX las exige: se leen de la propia caché
            missing = [k for k in keys if k not in originals]
            if missing:
                raise ValueError(f"No se encontraron en la caché MySQL las claves {missing}; no se borra nada")
            statements = [originals[k] for k in keys]
        elif len(statements) != len(keys):
            raise ValueError("Delete requiere un statement por cada clave (o ninguno, y se leen de la caché)")
    body_extra: dict[str, Any] = {"keys": keys, "statements": statements}
    payload = _editor_payload(action, **body_extra)
    items = [{"key": k, "statement": originals[k]} for k in keys if k in originals]
    backup_id = backups.save("mysqlcache", "-".join(map(str, keys))[:40], {"items": items, "accion": action},
                             "edit_mysql_cache") if items else None
    response = await client._post("/api/edit/mysqlcache", content=payload, headers=settings_engine.JSON_HEADERS)
    return _envelope(response, backup_id, "la sentencia")


@write_tool
async def edit_mysql_cache(action: Literal["Edit", "Delete"], keys: list[int], statements: list[str] | None = None) -> str:
    """Edita o borra comandos SQL pendientes en la caché de reintentos de MySQL (ver mysql_cache). Edit: exactamente 1 clave y
    1 statement nuevo (CumulusMX solo procesa el primero). Delete: una o varias claves; las sentencias (que CumulusMX exige)
    se leen de la propia caché si no las indicas. Guarda antes una copia de las sentencias (copia_id; una edición es
    restaurable)."""
    return await _edit_mysql_cache(action, keys, statements)


RECORD_FIELDS_COMMON = (
    "highTemp", "lowTemp", "highDewPoint", "lowDewPoint", "highApparentTemp", "lowApparentTemp", "highFeelsLike",
    "lowFeelsLike", "highHumidex", "lowWindChill", "highHeatIndex", "highMinTemp", "lowMaxTemp", "highDailyTempRange",
    "lowDailyTempRange", "highHumidity", "lowHumidity", "highBarometer", "lowBarometer", "highGust", "highWind",
    "highWindRun", "highRainRate", "highHourlyRain", "highDailyRain", "highRain24h", "highMonthlyRain",
    "longestDryPeriod", "longestWetPeriod", "highBgt", "highWbgt",
)


RECORD_EDIT_FIELDS: dict[str, tuple[str, ...]] = {
    "alltime": RECORD_FIELDS_COMMON,
    "monthly": RECORD_FIELDS_COMMON,
    "thisyear": RECORD_FIELDS_COMMON,
    "thismonth": tuple(f for f in RECORD_FIELDS_COMMON if f != "highMonthlyRain"),
}


# CumulusMX lee estos récords con int.Parse: "23.0" o "23,0" dan error 500, hay que enviar "23"
INTEGER_RECORD_FIELDS = frozenset({"highHumidity", "lowHumidity", "longestDryPeriod", "longestWetPeriod"})


async def _station_decimal_separator() -> str:
    """Separador decimal que usa la estación (CumulusMX parsea los valores editados con su cultura regional)."""
    text = await client._get("/api/tags/process.json?temp")
    m = re.search(r'"temp"\s*:\s*"\s*-?\d+([.,])\d', text)
    return m.group(1) if m else "."


async def _edit_records(scope: str, field: str, value: float, time: str, month: int | None) -> str:
    """Implementación de edit_records (también la usa restore_backup)."""
    if scope not in RECORD_EDIT_FIELDS:
        raise ValueError(f"scope debe ser uno de {sorted(RECORD_EDIT_FIELDS)}")
    if field not in RECORD_EDIT_FIELDS[scope]:
        raise ValueError(f"field '{field}' no válido para {scope}. Válidos: {list(RECORD_EDIT_FIELDS[scope])}")
    if scope == "monthly" and not (month and 1 <= month <= 12):
        raise ValueError("scope='monthly' requiere month entre 1 y 12")
    if field in INTEGER_RECORD_FIELDS:
        if float(value) != int(value):
            raise ValueError(f"'{field}' es un número entero (CumulusMX no admite decimales): {value}")
        text_value = str(int(value))
    else:
        text_value = str(value).replace(".", await _station_decimal_separator())
    name = f"{month}-{field}" if scope == "monthly" else field
    body = f"name={name}&value={text_value}&time={urllib.parse.quote_plus(time)}"
    backup_id = None
    if backups.enabled():
        before = await client._get(f"/api/edit/{scope}records.json")
        if not before.startswith("ERROR"):
            entry = capture.record_entry(before, scope, field, month)
            content = {"scope": scope, "field": field, "month": month, "accion": "Edit", **(entry or {"raw": before})}
            backup_id = backups.save("records", f"{scope}-{field}", content, "edit_records")
    response = await client._post(f"/api/edit/{scope}", content=body, headers=settings_engine.FORM_HEADERS)
    return _envelope(response, backup_id, "el récord")


@write_tool
async def edit_records(scope: Literal["alltime", "monthly", "thismonth", "thisyear"], field: str, value: float, time: str,
                       month: int | None = None) -> str:
    """Corrige un récord de la estación. scope: alltime | monthly (requiere month 1-12) | thismonth | thisyear. field:
    highTemp, lowTemp, highDewPoint, lowDewPoint, highApparentTemp, lowApparentTemp, highFeelsLike, lowFeelsLike,
    highHumidex, lowWindChill, highHeatIndex, highMinTemp, lowMaxTemp, highDailyTempRange, lowDailyTempRange,
    highHumidity, lowHumidity, highBarometer, lowBarometer, highGust, highWind, highWindRun, highRainRate,
    highHourlyRain, highDailyRain, highRain24h, highMonthlyRain (no en thismonth), longestDryPeriod, longestWetPeriod,
    highBgt, highWbgt. time: 'dd/MM/yyyy HH:mm' (récords con hora), 'dd/MM/yyyy' (récords diarios) o 'MMM yyyy' (lluvia
    mensual), en el formato regional del servidor. El valor se envía con el separador decimal de la estación. Guarda antes
    el valor y la hora anteriores de ese récord (copia_id; restaurable con restore_backup)."""
    return await _edit_records(scope, field, value, time, month)


STATION_INPUT_PROTOCOLS: dict[str, tuple[str, ...]] = {
    "ecowitt": ("GET", "POST"), "ecowittextra": ("GET", "POST"), "json": ("POST",), "jsonextra": ("POST",),
    "wunderground": ("GET",), "ambient": ("GET",), "ambientextra": ("GET",),
}


@write_tool
async def station_input(protocol: Literal["ecowitt", "ecowittextra", "json", "jsonextra", "wunderground", "ambient",
                                          "ambientextra"],
                        method: Literal["GET", "POST"] = "POST", body: str = "", query: dict[str, str] | None = None,
                        content_type: str = "application/x-www-form-urlencoded") -> str:
    """Envía una lectura a la entrada HTTP de CumulusMX, como haría una estación o consola (/station/<protocolo>): útil para
    probar y depurar la configuración de una estación 'HTTP Ecowitt', 'HTTP Ambient' o 'JSON'. POST: ecowitt, ecowittextra,
    json, jsonextra (cuerpo en body). GET: ecowitt, ecowittextra, wunderground, ambient, ambientextra (parámetros en
    query). Falla con 503 si la estación configurada no es de ese tipo. CAUTELA: los datos enviados se registran como
    observaciones reales."""
    allowed = STATION_INPUT_PROTOCOLS.get(protocol)
    if allowed is None or method not in allowed:
        raise ValueError(f"{protocol} admite {STATION_INPUT_PROTOCOLS.get(protocol)}")
    if (err := safety._check_write_enabled()):
        return err
    path = f"/station/{protocol}"
    if method == "GET":
        return await client._get(path, {str(k)[:60]: str(v)[:300] for k, v in (query or {}).items()} or None)
    return await client._post(path, content=body, headers={"Content-Type": content_type})
