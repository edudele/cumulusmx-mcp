"""Tools de récords y consultas del dayfile."""
import asyncio
import datetime
import json
import re
import urllib.parse
from typing import Literal


from .. import client
from .. import settings_engine
from .. import utils
from ..app import READ_ONLY, mcp


@mcp.tool(annotations=READ_ONLY)
async def records(scope: Literal["alltime", "thismonth", "thisyear", "month"],
                  kind: Literal["temperature", "humidity", "pressure", "wind", "rain"],
                  month: int = 0) -> str:
    """Récords de la estación. Si scope='month' indica month (1-12)."""
    if scope == "month":
        if not 1 <= month <= 12:
            raise ValueError("month debe estar entre 1 y 12")
        return await client._get(f"/api/records/month/{month}/{kind}.json")
    return await client._get(f"/api/records/{scope}/{kind}.json")


@mcp.tool(annotations=READ_ONLY)
async def records_period(startdate: str, enddate: str) -> str:
    """Récords calculados para un periodo específico (yyyy-mm-dd). Años permitidos: 2000-2050."""
    for d, n in ((startdate, "startdate"), (enddate, "enddate")):
        if not re.fullmatch(r"20[0-5]\d-\d{2}-\d{2}", d):
            raise ValueError(f"{n} debe tener formato yyyy-mm-dd y estar entre 2000 y 2050")
        try:
            datetime.datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise ValueError("fecha inválida, usa yyyy-mm-dd")
    return await client._get("/api/records/thisperiod", {"startdate": startdate, "enddate": enddate})


@mcp.tool(annotations=READ_ONLY)
async def records_all(scope: Literal["alltime", "thismonth", "thisyear", "month"],
                      month: int = 0,
                      strip_html: bool = True) -> str:
    """Todos los récords (temperatura, humedad, presión, viento, lluvia) del ámbito elegido en una llamada.
    Si scope='month', indicar month (1-12)."""
    keys = ("temperature", "humidity", "pressure", "wind", "rain")
    if scope == "month" and not 1 <= month <= 12:
        raise ValueError("month debe estar entre 1 y 12")
    paths = [f"/api/records/month/{month}/{k}.json" if scope == "month" else f"/api/records/{scope}/{k}.json"
             for k in keys]
    texts = await asyncio.gather(*(client._get(p) for p in paths))
    res = {}
    for k, t in zip(keys, texts):
        try:
            res[k] = json.loads(t)
        except ValueError:
            res[k] = t
    s = json.dumps(res, ensure_ascii=False)
    return utils._clean(s) if strip_html else s


@mcp.tool(annotations=READ_ONLY)
async def records_edit_data(scope: Literal["alltime", "monthly", "thismonth", "thisyear"],
                            source: Literal["records", "dayfile", "logfile"] = "records") -> str:
    """Datos de la pantalla de edición de récords de CumulusMX. source='records': valores de récord actuales (editables con
    edit_records); 'dayfile': candidatos calculados desde el dayfile; 'logfile': desde los logs de intervalo (más lento)."""
    suffix = {"records": "", "dayfile": "dayfile", "logfile": "logfile"}[source]
    return await client._get(f"/api/edit/{scope}records{suffix}.json")


QUERY_PROPERTIES = (
    "HighGust", "WindRun", "HighAvgWind", "LowTemp", "HighTemp", "AvgTemp", "HighHeatIndex", "HighAppTemp",
    "LowAppTemp", "LowWindChill", "HighDewPoint", "LowDewPoint", "HighFeelsLike", "LowFeelsLike", "HighHumidex",
    "LowPress", "HighPress", "HighRainRate", "TotalRain", "HighHourlyRain", "HighRain24h", "LowHumidity",
    "HighHumidity", "SunShineHours", "HighSolar", "HighUv", "ET", "HeatingDegreeDays", "CoolingDegreeDays",
    "ChillHours", "HighBgt", "HighWbgt",
)


QUERY_FUNCTIONS = ("min", "max", "sum", "avg", "count")


QUERY_OPERATORS = (">", "<", ">=", "<=", "=", "<>")


@mcp.tool(annotations=READ_ONLY)
async def records_query(dataname: str, function: Literal["min", "max", "sum", "avg", "count"], period: str = "ThisYear",
                        start: str = "", end: str = "", where: Literal[">", "<", ">=", "<=", "=", "<>"] | None = None,
                        value: float = 0.0, countfunction: Literal["min", "max", "sum", "avg", "count"] | None = None) -> str:
    """Consulta del dayfile ejecutada por el propio CumulusMX (misma función que 'Consultas' de su web). dataname:
    HighTemp, LowTemp, AvgTemp, TotalRain, HighGust, WindRun, HighAvgWind, HighHeatIndex, HighAppTemp, LowAppTemp,
    LowWindChill, HighDewPoint, LowDewPoint, HighFeelsLike, LowFeelsLike, HighHumidex, LowPress, HighPress, HighRainRate,
    HighHourlyRain, HighRain24h, LowHumidity, HighHumidity, SunShineHours, HighSolar, HighUv, ET, HeatingDegreeDays,
    CoolingDegreeDays, ChillHours, HighBgt, HighWbgt. function: min|max|sum|avg|count. period: 'ThisYear', 'ThisMonth',
    'ThisDay', 'Year-N', 'Month-N', 'Day-N' (N periodos atrás) o 'User' con start/end (formato de CumulusMX, p. ej.
    dd/MM/yyyy). where+value filtran días (p. ej. where='>=' value=30 con function='count'). Devuelve {value, time}."""
    if dataname not in QUERY_PROPERTIES:
        raise ValueError(f"dataname debe ser uno de {list(QUERY_PROPERTIES)}")
    if function not in QUERY_FUNCTIONS or (countfunction is not None and countfunction not in QUERY_FUNCTIONS):
        raise ValueError(f"function/countfunction deben ser uno de {list(QUERY_FUNCTIONS)}")
    if where is not None and where not in QUERY_OPERATORS:
        raise ValueError(f"where debe ser uno de {list(QUERY_OPERATORS)}")
    if not re.fullmatch(r"[A-Za-z]{3,10}(-?\d{1,4})?", period):
        raise ValueError("period inválido (ThisYear, ThisMonth, ThisDay, Year-N, Month-N, Day-N o User)")
    if period == "User" and not (start and end):
        raise ValueError("period='User' requiere start y end")
    body = {"dataname": dataname, "function": function, "where": where, "value": value, "startsel": period,
            "start": start or None, "end": end or None, "countfunction": countfunction}
    return await client._post("/api/records/query/dayfile.json", content="json=" + urllib.parse.quote(json.dumps(body)),
                       headers=settings_engine.FORM_HEADERS)
