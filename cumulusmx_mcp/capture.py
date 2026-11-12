"""Lectura del estado ANTERIOR de lo que se va a editar, para guardarlo en las copias de seguridad.

Formatos verificados en el código de CumulusMX 5.2.0 (WeatherStation.cs, MySqlFunctions.cs, DataEditor.cs):
- /api/data/dayfile        -> {"data": [[nº de línea, campos...]]}           (start = índice, 0-relativo)
- /api/data/logfile        -> {"data": [[nº de línea en el fichero, campos...]]}; from/to = fechas, search = texto de la línea
- /api/data/recentdata     -> {"data": [["dd/MM/yy HH:mm", <marca de tiempo>, ...]]}; search = texto del CSV
- /api/data/mysqlcache.json-> {"data": [[clave, "sentencia"]]}
- /api/edit/<ámbito>records.json -> objeto plano con claves "<campo>Val" y "<campo>Time" (mensuales: "<mes>-<campo>Val")
Todas las funciones devuelven None (o {}) si no pueden leer el dato: una copia que falla no debe impedir la operación.
"""
import datetime
import json
from typing import Any

from . import client, config
from .config import logger


async def _rows(path: str, params: dict[str, Any]) -> list[list[Any]]:
    data = await client._json(path, {"draw": 1, "search[value]": "", **params})
    return data["data"] if isinstance(data, dict) and isinstance(data.get("data"), list) else []


async def dayfile_line(line: int) -> list[Any] | None:
    """Fila [nº de línea, campos...] del dayfile, o None."""
    try:
        rows = await _rows("/api/data/dayfile", {"start": line - 1, "length": 1})
        return rows[0] if rows and int(rows[0][0]) == line else None
    except Exception as exc:
        logger.warning("No se pudo leer la línea %s del dayfile para la copia previa: %s", line, exc)
        return None


def _day_window(ts: int) -> tuple[str, str]:
    """Ventana de fechas (día anterior, día siguiente) alrededor de una marca de tiempo: cubre la zona horaria del servidor."""
    tz = datetime.timezone.utc
    if config.CUMULUS_TIMEZONE:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(config.CUMULUS_TIMEZONE)
    day = datetime.datetime.fromtimestamp(ts, tz).date()
    one = datetime.timedelta(days=1)
    return (day - one).isoformat(), (day + one).isoformat()


async def datalog_line(line: int, ts: str, extra: bool) -> list[Any] | None:
    """Fila [nº de línea, campos...] del log de intervalo cuyo TS (segundos Unix) es `ts`, comprobando que está en `line`."""
    try:
        start, end = _day_window(int(ts))
        rows = await _rows("/api/data/extralogfile" if extra else "/api/data/logfile",
                           {"from": start, "to": end, "start": 0, "length": 50, "search[value]": str(ts)})
        for row in rows:
            if int(row[0]) == line and str(row[2]) == str(ts):     # row[1] = DateTime, row[2] = TS
                return row
    except Exception as exc:
        logger.warning("No se pudo leer la línea %s del log para la copia previa: %s", line, exc)
    return None


async def recent_row(timestamp: str) -> list[Any] | None:
    """Registro de datos recientes con esa marca de tiempo (segundo elemento de la fila), o None."""
    try:
        rows = await _rows("/api/data/recentdata", {"start": 0, "length": 20, "search[value]": str(timestamp)})
        return next((r for r in rows if len(r) > 1 and str(r[1]) == str(timestamp)), None)
    except Exception as exc:
        logger.warning("No se pudo leer el registro %s de datos recientes para la copia previa: %s", timestamp, exc)
        return None


async def mysql_cache_all() -> list[tuple[int, str]] | None:
    """Toda la caché MySQL de CumulusMX como [(clave, sentencia)], recorriendo las páginas. None si no se pudo leer."""
    out: list[tuple[int, str]] = []
    try:
        start = 0
        while True:
            data = await client._json("/api/data/mysqlcache.json", {"draw": 1, "start": start, "length": 500,
                                                                    "search[value]": ""})
            rows = data.get("data", []) if isinstance(data, dict) else []
            out += [(int(key), statement) for key, statement in rows]
            start += 500
            if not rows or start >= int(data.get("recordsTotal", 0)):
                return out
    except Exception as exc:
        logger.warning("No se pudo leer la caché MySQL: %s", exc)
        return None


async def mysql_cache_statements(keys: list[int]) -> dict[int, str]:
    """Sentencias de la caché MySQL para esas claves. Faltan las claves que no existen."""
    found: dict[int, str] = {}
    for key, statement in await mysql_cache_all() or []:
        if key in keys:
            found.setdefault(key, statement)
    return found


def record_entry(raw_text: str, scope: str, field: str, month: int | None) -> dict[str, str] | None:
    """Valor y hora actuales de un récord a partir del texto de /api/edit/<ámbito>records.json, o None."""
    try:
        data = json.loads(raw_text)
        prefix = f"{month}-" if scope == "monthly" else ""
        value, when = data[f"{prefix}{field}Val"], data[f"{prefix}{field}Time"]
        return {"valor": str(value), "hora": str(when)}
    except (ValueError, KeyError, TypeError):
        return None


def parse_regional_number(text: str) -> float:
    """'34,6' / '34.6' / '1.234,5' / '1,234.5' -> float (CumulusMX devuelve los números con el formato regional)."""
    t = text.strip().replace(" ", "")
    if "," in t and "." in t:
        decimal = "," if t.rfind(",") > t.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        t = t.replace(thousands, "").replace(decimal, ".")
    else:
        t = t.replace(",", ".")
    return float(t)
