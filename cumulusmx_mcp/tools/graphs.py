"""Tools de gráficas y datos por intervalo."""
import datetime
import html
import json
import re
import urllib.parse
from typing import Any, Literal


from .. import client
from .. import config
from .. import settings_engine
from .. import utils
from ..app import READ_ONLY, mcp


@mcp.tool(annotations=READ_ONLY)
async def graph_data(name: str, start: str = "", end: str = "") -> str:
    """Serie temporal para gráficos (últimas ~24 h por defecto). name (sin .json):
    tempdata, winddata, raindata, pressdata, wdirdata, humdata, solardata, airqualitydata, extratemp, extrahum,
    extradew, soiltemp, soilmoist, soilec, leafwetness, usertemp, co2sensor, laserdepth, snow24h;
    diarias: dailyrain, sunhours, dailytemp; meta: units, graphconfig, availabledata;
    por intervalo (con start/end): intvtemp, intvwind, intvrain, intvpress, intvhum, intvsolar, intvairquality,
    intvextratemp, intvextrahum, intvextradew, intvsoiltemp, intvsoilmoist, intvsoilec, intvleafwetness,
    intvusertemp, intvco2sensor, intvlaserdepth, intvsnow24h. Las series de sensores extra solo existen si el
    sensor está configurado. start/end opcionales: 'yyyy-MM-dd' o 'yyyy-MM-dd HH:mm' (hora local)."""
    if not re.fullmatch(r"[a-z0-9]{3,30}", name):
        raise ValueError("name inválido")
    params = {}
    for k, v in (("start", start), ("end", end)):
        if v:
            if not utils.DATE_RE.match(v):
                raise ValueError(f"{k} con formato inválido")
            params[k] = v
    return await client._get(f"/api/graphdata/{name}.json", params)


@mcp.tool(annotations=READ_ONLY)
async def daily_graph_data(name: str) -> str:
    """Series diarias de todo el histórico. name (sin .json): tempdata, winddata, raindata, pressdata, wdirdata,
    humdata, solardata, degdaydata, dailysnow, tempsumdata, chillhrsdata, units, graphconfig."""
    if not re.fullmatch(r"[a-z0-9]{3,30}", name):
        raise ValueError("name inválido")
    return await client._get(f"/api/dailygraphdata/{name}.json")


# Series de /api/graphdata (nombres según la documentación de CumulusMX) -> grupo de unidades de units.json
_SERIES_UNIT_GROUP = {
    "intemp": "temp", "dew": "temp", "apptemp": "temp", "feelslike": "temp", "wchill": "temp",
    "heatindex": "temp", "temp": "temp", "mintemp": "temp", "maxtemp": "temp", "avgtemp": "temp",
    "wgust": "wind", "wspeed": "wind", "rfall": "rain", "dailyrain": "rain", "rrate": "rain/h", "press": "press",
}


_SERIES_FIXED_UNIT = {
    "hum": "%", "inhum": "%", "SolarRad": "W/m²", "CurrentSolarMax": "W/m²",
    "bearing": "°", "avgbearing": "°", "sunshours": "h",
}


async def _graph_units() -> dict[str, str]:
    """Unidades configuradas en la estación (/api/graphdata/units.json). Si falla o el formato no es el esperado,
    devuelve {} para que graph_summary siga funcionando sin unidades."""
    try:
        raw = await client._json("/api/graphdata/units.json")
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(v, dict):  # formato antiguo de la wiki: {"temp": {"units": "C", "decimals": 1}}
            v = v.get("units")
        if isinstance(v, str) and v:
            out[k] = html.unescape(v)
    return out


def _series_unit(series: str, units: dict[str, str]) -> str | None:
    if series in _SERIES_FIXED_UNIT:
        return _SERIES_FIXED_UNIT[series]
    group = _SERIES_UNIT_GROUP.get(series)
    if group is None:
        return None
    suffix = "/h" if group.endswith("/h") else ""
    base = units.get(group.removesuffix("/h"))
    if not base:
        return None
    if base in ("C", "F"):
        base = "°" + base
    return base + suffix


@mcp.tool(annotations=READ_ONLY)
async def graph_summary(name: str, start: str = "", end: str = "", points: int = 0) -> str:
    """Resumen estadístico de una serie de gráfica (mín, máx, media, último valor, con hora ISO) en vez de miles de puntos.
    name: tempdata, winddata, raindata, pressdata, humdata, solardata, wdirdata, o intvtemp, intvwind, intvrain,
    intvpress, intvhum, intvsolar (estas con start/end 'yyyy-MM-dd' o 'yyyy-MM-dd HH:mm', hora local).
    points>0 devuelve además la serie reducida a ese nº de puntos (máx 300)."""
    if not re.fullmatch(r"[a-z0-9]{3,30}", name):
        raise ValueError("name inválido")
    params = {}
    for k, v in (("start", start), ("end", end)):
        if v:
            if not utils.DATE_RE.match(v):
                raise ValueError(f"{k} con formato inválido")
            params[k] = v
    j = await client._json(f"/api/graphdata/{name}.json", params)
    units = await _graph_units()
    out = {}
    for serie, pts in (j.items() if isinstance(j, dict) else []):
        vals = [(p[0], utils._num(p[1])) for p in pts if isinstance(p, list) and len(p) >= 2 and utils._num(p[1]) is not None]
        if not vals:
            continue
        iso = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M")
        mn, mx = min(vals, key=lambda x: x[1]), max(vals, key=lambda x: x[1])
        d = {"n": len(vals), "min": mn[1], "min_en": iso(mn[0]), "max": mx[1], "max_en": iso(mx[0]),
             "media": round(sum(v for _, v in vals) / len(vals), 2), "ultimo": vals[-1][1],
             "ultimo_en": iso(vals[-1][0]), "primero_en": iso(vals[0][0]), "nota_hora": "ISO en UTC"}
        if (unidad := _series_unit(serie, units)):
            d["unidad"] = unidad
        if points > 0:
            step = max(1, len(vals) // min(points, 300))
            d["serie"] = [[iso(t), v] for t, v in vals[::step]]
        out[serie] = d
    return json.dumps(out, ensure_ascii=False)


LOG_FILE_FIELDS = [
    "DateTime", "TS", "Temperature", "Humidity", "DewPoint", "WindSpeed", "RecentHighGust", "AverageWindBearing",
    "RainfallRate", "RainfallSoFar", "SeaLevelPressure", "RainfallCounter", "InsideTemperature", "InsideHumidity",
    "CurrentGust", "WindChill", "HeatIndex", "UvIndex", "SolarRadiation", "Evapotranspiration",
    "AnnualEvapotranspiration", "ApparentTemperature", "MaxSolarRadiation", "HoursOfSunshine", "WindBearing",
    "Rg-11Rain", "RainSinceMidnight", "FeelsLike", "Humidex", "BGT", "WBGT",
]


EXTRA_LOG_FIELDS = [
    "DateTime", "TS",
    *[f"Temp{i}" for i in range(1, 11)], *[f"Hum{i}" for i in range(1, 11)], *[f"Dewpoint{i}" for i in range(1, 11)],
    "SoilTemp1", "SoilTemp2", "SoilTemp3", "SoilTemp4", "SoilMoist1", "SoilMoist2", "SoilMoist3", "SoilMoist4",
    "na1", "na2", "LeafWet1", "LeafWet2",
    *[f"SoilTemp{i}" for i in range(5, 17)], *[f"SoilMoist{i}" for i in range(5, 17)],
    "AQ1Pm", "AQ2Pm", "AQ3Pm", "AQ4Pm", "AQ1PmAvg", "AQ2PmAvg", "AQ3PmAvg", "AQ4PmAvg",
    *[f"UserTemp{i}" for i in range(1, 9)],
    "CO2", "CO2Avg", "CO2Pm25", "CO2Pm25Avg", "CO2Pm10", "CO2Pm10Avg", "CO2Temp", "CO2Hum",
    "LaserDist1", "LaserDist2", "LaserDist3", "LaserDist4", "Snow24h",
    *[f"Temp{i}" for i in range(11, 17)], *[f"Hum{i}" for i in range(11, 17)], *[f"Dewpoint{i}" for i in range(11, 17)],
    "AQ1Pm10", "AQ2Pm10", "AQ3Pm10", "AQ4Pm10", *[f"SoilEC{i}" for i in range(1, 17)],
]


DAYFILE_FIELD_NAMES = [
    "Date", "HighWindGust", "HighGustBearing", "HighGustTime", "MinTemperature", "MinTempTime", "MaxTemperature",
    "MaxTempTime", "MinPressure", "MinPressureTime", "MaxPressure", "MaxPressureTime", "MaxRainfallRate",
    "MaxRainRateTime", "TotalRainfallToday", "AvgTemperatureToday", "TotalWindRun", "HighAverageWindSpeed",
    "HighAvgWindSpeedTime", "LowHumidity", "LowHumidityTime", "HighHumidity", "HighHumidityTime",
    "TotalEvapotranspiration", "TotalHoursOfSunshine", "HighHeatIndex", "HighHeatIndexTime", "HighApparentTemperature",
    "HighAppTempTime", "LowApparentTemperature", "LowAppTempTime", "High1hRain", "High1hRainTime", "LowWindChill",
    "LowWindChillTime", "HighDewPoint", "HighDewPointTime", "LowDewPoint", "LowDewPointTime", "DominantWindBearing",
    "HeatingDegreeDays", "CoolingDegreeDays", "HighSolarRad", "HighSolarRadTime", "HighUv-I", "HighUv-ITime",
    "HighFeelsLike", "HighFeelsLikeTime", "LowFeelsLike", "LowFeelsLikeTime", "HighHumidex", "HighHumidexTime",
    "ChillHours", "High24hRain", "High24hRainTime", "HighBgt", "HighBgtTime", "HighWbgt", "HighWbgtTime",
]


def _field_indices(fields: list[str], kind: str) -> list[int]:
    """Nombres (o índices numéricos) -> índices de campo de CumulusMX. En 'interval' los campos del log de sensores
    extra suman 1000, como espera la API."""
    if not fields:
        raise ValueError("fields no puede estar vacío")
    lookup: dict[str, int] = {}
    if kind == "interval":
        for i, n in enumerate(EXTRA_LOG_FIELDS):
            lookup.setdefault(n.lower(), 1000 + i)
        for i, n in enumerate(LOG_FILE_FIELDS):
            lookup[n.lower()] = i  # el log principal prevalece si un nombre existe en ambos
    else:
        for i, n in enumerate(DAYFILE_FIELD_NAMES):
            lookup[n.lower()] = i
    out: list[int] = []
    for f in fields:
        key = str(f).strip().lower()
        if key.isdigit():
            out.append(int(key))
        elif key in lookup:
            out.append(lookup[key])
        else:
            raise ValueError(f"campo desconocido '{f}'. Válidos: {sorted({n for n in lookup})[:60]}...")
    return out


def _to_epoch(value: str, end_of_day: bool = False) -> int:
    """Segundos Unix tal cual, o una fecha/hora de la estación ('yyyy-mm-dd' / 'yyyy-mm-dd HH:MM') convertida con
    CUMULUS_TIMEZONE (zona IANA de la estación, p. ej. Europe/Madrid)."""
    v = str(value).strip()
    if re.fullmatch(r"\d{9,11}", v):
        return int(v)
    if not config.CUMULUS_TIMEZONE:
        raise ValueError("Para usar fechas legibles define CUMULUS_TIMEZONE (zona IANA de la estación, p. ej. "
                         "Europe/Madrid) o pasa segundos Unix.")
    import zoneinfo
    if utils.DATE_RE.match(v):
        fmt = "%Y-%m-%d %H:%M" if " " in v else "%Y-%m-%d"
    else:
        raise ValueError(f"fecha inválida '{value}': usa yyyy-mm-dd, 'yyyy-mm-dd HH:MM' o segundos Unix")
    dt = datetime.datetime.strptime(v, fmt)
    if end_of_day and fmt == "%Y-%m-%d":
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.replace(tzinfo=zoneinfo.ZoneInfo(config.CUMULUS_TIMEZONE)).timestamp())


_NUM_CELL_RE = re.compile(r"^-?\d+([.,]\d+)?$")


def _parse_table(raw: str, max_rows: int) -> dict[str, Any] | str:
    """Respuesta de intervaldata/dailydata ([["Date/Time", nombres...], [fecha, valores...], ...]) -> campos + filas con
    números reales (acepta coma decimal regional). Si no tiene el formato esperado devuelve el texto original."""
    try:
        rows = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
        return raw

    def cell(c: Any) -> Any:
        if isinstance(c, str) and _NUM_CELL_RE.match(c):
            f = float(c.replace(",", "."))
            return int(f) if re.fullmatch(r"-?\d+", c) else f
        return c

    body = [[r[0], *[cell(c) for c in r[1:]]] for r in rows[1:] if isinstance(r, list) and r]
    return {"campos": rows[0][1:], "n_filas": len(body), "filas": body[:max_rows], "truncado": len(body) > max_rows}


@mcp.tool(annotations=READ_ONLY)
async def interval_data(start: str, end: str, fields: list[str], max_rows: int = 500) -> str:
    """Datos de los logs de intervalo (cada pocos minutos) de un rango, con SOLO los campos que pidas (sin paginar).
    fields: nombres del log principal (Temperature, Humidity, DewPoint, WindSpeed, RecentHighGust, RainfallRate,
    SeaLevelPressure, UvIndex, SolarRadiation, FeelsLike, Humidex, BGT...) o del log de sensores extra (Temp1, SoilTemp1,
    SoilMoist1, LeafWet1, CO2, ...), o índices numéricos. start/end: segundos Unix, o 'yyyy-mm-dd' / 'yyyy-mm-dd HH:MM' si
    el servidor tiene CUMULUS_TIMEZONE (zona horaria de la estación). Devuelve campos y filas con números reales."""
    s, e = _to_epoch(start), _to_epoch(end, end_of_day=True)
    if e < s:
        raise ValueError("end anterior a start")
    idx = ",".join(str(i) for i in _field_indices(fields, "interval"))
    raw = await client._get("/api/data/intervaldata.json", {"from": s, "to": e, "data": idx})
    out = _parse_table(raw, max(1, min(max_rows, 2000)))
    return raw if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def daily_data(start: str, end: str, fields: list[str], max_rows: int = 500) -> str:
    """Datos diarios (dayfile) de un rango con SOLO los campos oficiales de CumulusMX que pidas (nombres como
    MaxTemperature, MinTemperature, TotalRainfallToday, HighWindGust, TotalHoursOfSunshine, HighBgt... o índices).
    Alternativa a dayfile_range con los nombres originales; mismas reglas de fecha que interval_data."""
    s, e = _to_epoch(start), _to_epoch(end, end_of_day=True)
    if e < s:
        raise ValueError("end anterior a start")
    idx = ",".join(str(i) for i in _field_indices(fields, "daily"))
    raw = await client._get("/api/data/dailydata.json", {"from": s, "to": e, "data": idx})
    out = _parse_table(raw, max(1, min(max_rows, 2000)))
    return raw if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def graph_select(kind: Literal["chart", "period"], series: list[str], from_date: str, to_date: str,
                       colours: list[str] | None = None) -> str:
    """Gráfica personalizada de CumulusMX ('Select-a-Chart' y 'Select-a-Period'): devuelve las series pedidas entre dos
    fechas. series: nombres de series disponibles (consúltalos con graph_data('availabledata')). from_date/to_date:
    yyyy-mm-dd. colours: opcional (colores hexadecimales, uno por serie)."""
    if not series:
        raise ValueError("series no puede estar vacío")
    for d, n in ((from_date, "from_date"), (to_date, "to_date")):
        utils._check_date(d, n)
    payload = {"series": series, "colours": colours or ["#000000"] * len(series), "fromDate": from_date, "toDate": to_date}
    path = "selectachart" if kind == "chart" else "selectaperiod"
    return await client._post(f"/api/graphdata/{path}.json", content=urllib.parse.quote(json.dumps(payload)), headers=settings_engine.FORM_HEADERS)
