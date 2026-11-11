"""Tools de corrección de datos históricos y entrada de datos de estaciones HTTP."""
import json
import re
import urllib.parse
from typing import Any, Literal


from .. import backups, client
from .. import dayrows
from .. import safety
from .. import settings_engine
from ..app import write_tool
from ..config import logger


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


async def _backup_dayfile_line(line: int) -> str | None:
    """Guarda la línea actual del dayfile antes de editarla/borrarla. None si las copias están desactivadas o falla."""
    if not backups.enabled():
        return None
    try:
        data = await client._json("/api/data/dayfile", {"draw": 1, "start": line - 1, "length": 1, "search[value]": ""})
        row = data["data"][0]
        if int(row[0]) != line:
            raise ValueError(f"CumulusMX devolvió la línea {row[0]} en lugar de la {line}")
    except Exception as exc:
        logger.warning("No se pudo leer la línea %s del dayfile para la copia previa: %s", line, exc)
        return None
    return backups.save("dayfile", f"linea{line}", {"line": line, "row": row}, "edit_dayfile")


async def _edit_dayfile(action: str, line: int, values: list[str]) -> str:
    """Implementación de edit_dayfile (también la usa restore_backup). Devuelve un sobre JSON con la respuesta y la copia."""
    if line < 1 or not values:
        raise ValueError("line debe ser >= 1 y values no puede estar vacío")
    payload = _editor_payload(action, [line], [[str(v) for v in values]])
    backup_id = await _backup_dayfile_line(line)
    response = await client._post("/api/edit/dayfile", content=payload, headers=settings_engine.JSON_HEADERS)
    dayrows.invalidate_cache()
    result: dict[str, Any] = {"respuesta": response, "copia_id": backup_id}
    if backups.enabled() and backup_id is None:
        result["aviso"] = "No se pudo guardar la copia previa de la línea (ver el log del servidor)."
    return json.dumps(result, ensure_ascii=False)


@write_tool
async def edit_dayfile(action: Literal["Edit", "Delete"], line: int, values: list[str]) -> str:
    """Edita o borra UNA línea del dayfile.txt (corrección de datos históricos). line: número de línea (1 = primera; es el
    primer valor que devuelve la tool dayfile). values: los campos de esa línea tal como aparecen en dayfile.txt (fecha
    dd/MM/yy primero, sin el número de línea). CumulusMX comprueba que la fecha coincide con la de la línea indicada.
    Actualiza también SQLite y, si está activado 'update on edit', MySQL. Guarda antes una copia de la línea original
    (copia_id en la respuesta; se puede deshacer con restore_backup)."""
    return await _edit_dayfile(action, line, values)


@write_tool
async def edit_datalog(action: Literal["Edit", "Delete"], line: int, values: list[str], extra: bool = False) -> str:
    """Edita o borra una línea de un log mensual de intervalo (extra=True: log de sensores extra). El fichero se deduce del
    segundo campo (TS, segundos Unix) de values; line es la línea dentro de ese fichero (1 = primera). values: todos los
    campos de la línea del log. CumulusMX comprueba que el TS coincide con el de la línea. HAZ COPIA DE SEGURIDAD."""
    if line < 1 or not values:
        raise ValueError("line debe ser >= 1 y values no puede estar vacío")
    if (err := safety._check_write_enabled()):
        return err
    return await client._post("/api/edit/datalogs", content=_editor_payload(action, [line], [[str(v) for v in values]], extra=extra),
                       headers=settings_engine.JSON_HEADERS)


@write_tool
async def edit_recent_data(action: Literal["Edit", "Delete"], values: list[str]) -> str:
    """Edita o borra un registro de la tabla de datos recientes (SQLite) de CumulusMX. values: los campos del registro en
    el orden CSV de la tabla (consúltalo con recent_data)."""
    if not values:
        raise ValueError("values no puede estar vacío")
    if (err := safety._check_write_enabled()):
        return err
    return await client._post("/api/edit/recentdata", content=_editor_payload(action, data=[[str(v) for v in values]]),
                       headers=settings_engine.JSON_HEADERS)


@write_tool
async def edit_mysql_cache(action: Literal["Edit", "Delete"], keys: list[int], statements: list[str] | None = None) -> str:
    """Edita o borra comandos SQL pendientes en la caché de reintentos de MySQL (ver mysql_cache). keys: claves de los
    comandos; statements: nuevo SQL (solo para Edit, uno por clave)."""
    if not keys:
        raise ValueError("keys no puede estar vacío")
    if action == "Edit" and (not statements or len(statements) != len(keys)):
        raise ValueError("Edit requiere un statement por cada clave")
    if (err := safety._check_write_enabled()):
        return err
    extra: dict[str, Any] = {"keys": keys}
    if statements is not None:
        extra["statements"] = statements
    return await client._post("/api/edit/mysqlcache", content=_editor_payload(action, **extra), headers=settings_engine.JSON_HEADERS)


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


async def _station_decimal_separator() -> str:
    """Separador decimal que usa la estación (CumulusMX parsea los valores editados con su cultura regional)."""
    text = await client._get("/api/tags/process.json?temp")
    m = re.search(r'"temp"\s*:\s*"\s*-?\d+([.,])\d', text)
    return m.group(1) if m else "."


@write_tool
async def edit_records(scope: Literal["alltime", "monthly", "thismonth", "thisyear"], field: str, value: float, time: str,
                       month: int | None = None) -> str:
    """Corrige un récord de la estación. scope: alltime | monthly (requiere month 1-12) | thismonth | thisyear. field:
    highTemp, lowTemp, highDewPoint, lowDewPoint, highApparentTemp, lowApparentTemp, highFeelsLike, lowFeelsLike,
    highHumidex, lowWindChill, highHeatIndex, highMinTemp, lowMaxTemp, highDailyTempRange, lowDailyTempRange,
    highHumidity, lowHumidity, highBarometer, lowBarometer, highGust, highWind, highWindRun, highRainRate,
    highHourlyRain, highDailyRain, highRain24h, highMonthlyRain (no en thismonth), longestDryPeriod, longestWetPeriod,
    highBgt, highWbgt. time: 'dd/MM/yyyy HH:mm' (récords con hora), 'dd/MM/yyyy' (récords diarios) o 'MMM yyyy' (lluvia
    mensual), en el formato regional del servidor. El valor se envía con el separador decimal de la estación."""
    if scope not in RECORD_EDIT_FIELDS:
        raise ValueError(f"scope debe ser uno de {sorted(RECORD_EDIT_FIELDS)}")
    if field not in RECORD_EDIT_FIELDS[scope]:
        raise ValueError(f"field '{field}' no válido para {scope}. Válidos: {list(RECORD_EDIT_FIELDS[scope])}")
    if scope == "monthly" and not (month and 1 <= month <= 12):
        raise ValueError("scope='monthly' requiere month entre 1 y 12")
    if (err := safety._check_write_enabled()):
        return err
    sep = await _station_decimal_separator()
    name = f"{month}-{field}" if scope == "monthly" else field
    body = f"name={name}&value={str(value).replace('.', sep)}&time={urllib.parse.quote_plus(time)}"
    backup_id = None
    if backups.enabled():  # los récords se guardan como texto de la pantalla de edición (restauración manual)
        before = await client._get(f"/api/edit/{scope}records.json")
        if not before.startswith("ERROR"):
            backup_id = backups.save("records", scope, before, "edit_records")
    response = await client._post(f"/api/edit/{scope}", content=body, headers=settings_engine.FORM_HEADERS)
    result: dict[str, Any] = {"respuesta": response, "copia_id": backup_id}
    if backups.enabled() and backup_id is None:
        result["aviso"] = "No se pudo guardar la copia previa de los récords (ver el log del servidor)."
    return json.dumps(result, ensure_ascii=False)


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
