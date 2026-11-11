"""Histórico diario (dayfile): columnas, caché en memoria, estadísticas básicas."""
import asyncio
import datetime
import operator
import statistics
import time
from typing import Any, Iterable, Literal


from . import client
from . import config
from . import utils


DAYFILE_COLS = [
    "n", "fecha", "racha_max", "dir_racha", "hora_racha", "temp_min", "hora_temp_min",
    "temp_max", "hora_temp_max", "pres_min", "hora_pres_min", "pres_max", "hora_pres_max",
    "lluvia_rate_max", "hora_lluvia_rate", "lluvia_total", "temp_media", "recorrido_viento",
    "viento_medio_max", "hora_viento_medio_max", "hum_min", "hora_hum_min", "hum_max", "hora_hum_max",
    "ET", "horas_sol", "indice_calor_max", "hora_ic", "temp_aparente_max", "hora_ta_max",
    "temp_aparente_min", "hora_ta_min", "lluvia_horaria_max", "hora_lluvia_horaria",
    "sensacion_viento_min", "hora_sv_min", "rocio_max", "hora_rocio_max", "rocio_min", "hora_rocio_min",
    "dir_viento_dominante", "grados_dia_calef", "grados_dia_refr", "solar_max", "hora_solar_max",
    "uv_max", "hora_uv_max", "feels_max", "hora_feels_max", "feels_min", "hora_feels_min",
    "humidex_max", "hora_humidex_max", "horas_frio",
    # Añadidas en versiones recientes de CumulusMX (orden oficial: High24hRain, HighBgt, HighWbgt)
    "lluvia_24h_max", "hora_lluvia_24h", "bgt_max", "hora_bgt_max", "wbgt_max", "hora_wbgt_max",
]


CANONICAL_COLUMNS = (
    "fecha", "temp_min", "temp_max", "temp_media",
    "lluvia_total", "racha_max", "pres_min", "pres_max",
    "hum_min", "hum_max", "horas_sol",
)


COLUMN_ALIASES: dict[str, str] = {
    "date": "fecha",
    "mintemp": "temp_min",
    "maxtemp": "temp_max",
    "avgtemp": "temp_media",
    "temp": "temp_media",
    "rain": "lluvia_total",
    "precipitation": "lluvia_total",
    "precip": "lluvia_total",
    "wind": "racha_max",
    "gust": "racha_max",
    "windgust": "racha_max",
    "press": "pres_min",
    "pressure": "pres_min",
    "minpress": "pres_min",
    "maxpress": "pres_max",
    "hum": "hum_min",
    "humidity": "hum_min",
    "minhum": "hum_min",
    "maxhum": "hum_max",
    "sun": "horas_sol",
    "sunhours": "horas_sol",
    "sol": "horas_sol",
    "solar": "solar_max",
    "uv": "uv_max",
    "et": "ET",
    "rain24h": "lluvia_24h_max",
    "bgt": "bgt_max",
    "wbgt": "wbgt_max",
}


_DAYFILE_LOWER_MAP = {c.lower(): c for c in DAYFILE_COLS}


UNITS: dict[str, str] = {
    "temp_min": "°C", "temp_max": "°C", "temp_media": "°C",
    "lluvia_total": "mm", "racha_max": "km/h",
    "pres_min": "hPa", "pres_max": "hPa",
    "hum_min": "%", "hum_max": "%", "horas_sol": "h",
    "ET": "mm", "recorrido_viento": "km", "solar_max": "W/m²",
    "uv_max": "UV",
    "lluvia_24h_max": "mm", "bgt_max": "°C", "wbgt_max": "°C",
}


ADDITIVE_COLUMNS: frozenset[str] = frozenset({"lluvia_total", "horas_sol", "ET", "recorrido_viento"})


def resolve_columns(columns: list[str] | None) -> list[str]:
    """Convierte alias a nombres canónicos.
    - Ignora mayúsculas y espacios.
    - Elimina duplicados conservando el orden.
    - Garantiza que 'fecha' va siempre en primera posición.
    - Lanza ValueError si hay columnas desconocidas, listándolas junto a las válidas.
    """
    if not columns:
        return list(CANONICAL_COLUMNS)

    resolved: list[str] = []
    unknown: list[str] = []
    for raw in columns:
        clean = raw.strip().lower()
        canon = COLUMN_ALIASES.get(clean, clean)
        actual = _DAYFILE_LOWER_MAP.get(canon.lower())
        if actual is None:
            unknown.append(raw)
        elif actual not in resolved:
            resolved.append(actual)

    if unknown:
        valid = sorted(set(DAYFILE_COLS) | set(COLUMN_ALIASES.keys()))
        raise ValueError(f"Columnas desconocidas: {unknown}. Válidas: {valid}")

    if "fecha" in resolved:
        resolved.remove("fecha")
    return ["fecha", *resolved]


_cached_dayfile_by_year: dict[int, list[dict]] = {}


_dayfile_cache_time: float = 0.0


_dayfile_lock = asyncio.Lock()


async def _fetch_all_dayfile_raw() -> list[dict]:
    rows, start = [], 0
    while True:
        j = await client._json("/api/data/dayfile", {"draw": 1, "start": start, "length": 500, "search[value]": ""})
        data = j.get("data", [])
        rows += data
        start += 500
        if not data or start >= j.get("recordsTotal", 0):
            break
    out = []
    for r in rows:
        d = dict(zip(DAYFILE_COLS, r))
        raw_date = d.get("fecha", "")
        dt_obj = None
        for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                dt_obj = datetime.datetime.strptime(raw_date, fmt).date()
                break
            except (ValueError, TypeError):
                continue
        if dt_obj is None:
            continue
        d["_date"] = dt_obj
        d["fecha_orig"] = raw_date
        d["fecha"] = dt_obj.isoformat()
        out.append(d)
    return out


async def _get_dayfile_rows(start_year: int | None = None, end_year: int | None = None) -> list[dict]:
    global _dayfile_cache_time
    now = time.monotonic()
    current_year = utils._today().year
    async with _dayfile_lock:
        needs_refresh = False
        if not _cached_dayfile_by_year:
            needs_refresh = True
        elif (now - _dayfile_cache_time) > config.CURRENT_YEAR_TTL_S:
            needs_refresh = True

        if needs_refresh:
            all_rows = await _fetch_all_dayfile_raw()
            by_year: dict[int, list[dict]] = {}
            for r in all_rows:
                y = r["_date"].year
                by_year.setdefault(y, []).append(r)
            _cached_dayfile_by_year.clear()
            _cached_dayfile_by_year.update(by_year)
            _dayfile_cache_time = now

    sy = start_year if start_year is not None else config.DATA_START_YEAR
    ey = end_year if end_year is not None else current_year
    result: list[dict] = []
    for y in sorted(_cached_dayfile_by_year.keys()):
        if sy <= y <= ey:
            result.extend(_cached_dayfile_by_year[y])
    return result


async def _all_dayfile() -> list[dict]:
    return await _get_dayfile_rows(start_year=2000, end_year=2050)


def _agg(rows: list[dict]) -> dict:
    if not rows:
        return {"dias": 0}

    def ext(col, fn):
        vals = [(utils._num(r.get(col)), r["_date"]) for r in rows if utils._num(r.get(col)) is not None]
        if not vals:
            return None
        v = fn(vals, key=lambda x: x[0])
        return {"valor": v[0], "fecha": str(v[1])}

    lluvia = [utils._num(r["lluvia_total"]) or 0 for r in rows]
    medias = [utils._num(r["temp_media"]) for r in rows if utils._num(r["temp_media"]) is not None]
    return {
        "dias": len(rows),
        "desde": str(rows[0]["_date"]), "hasta": str(rows[-1]["_date"]),
        "temp_max_absoluta": ext("temp_max", max), "temp_min_absoluta": ext("temp_min", min),
        "temp_media_del_periodo": round(sum(medias) / len(medias), 2) if medias else None,
        "lluvia_total": round(sum(lluvia), 1), "dias_con_lluvia(>=0.2)": sum(1 for x in lluvia if x >= 0.2),
        "dia_mas_lluvioso": ext("lluvia_total", max),
        "racha_max": ext("racha_max", max), "pres_max": ext("pres_max", max), "pres_min": ext("pres_min", min),
        "horas_sol_total": round(sum(utils._num(r["horas_sol"]) or 0 for r in rows), 1),
        "uv_max": ext("uv_max", max), "solar_max": ext("solar_max", max),
        "unidades": "las de la estación (ver station_info units)",
    }


def _valid(rows: Iterable[dict], var: str) -> list[tuple[str, float]]:
    out = []
    for r in rows:
        v = utils._num(r.get(var))
        f = r.get("fecha")
        if v is not None and f:
            out.append((str(f), v))
    return out


MAX_TIE_DATES = 10


def extreme(rows: Iterable[dict], var: str, mode: Literal["min", "max"]) -> dict | None:
    """Devuelve {'valor', 'fecha' (la más antigua), 'fechas' (las empatadas más antiguas, máx. MAX_TIE_DATES)
    y 'n_fechas' (total de empates) cuando hay más de MAX_TIE_DATES}."""
    pairs = _valid(rows, var)
    if not pairs:
        return None
    target = min(v for _, v in pairs) if mode == "min" else max(v for _, v in pairs)
    fechas = sorted(f for f, v in pairs if v == target)
    out = {"valor": round(target, 2), "fecha": fechas[0], "fechas": fechas[:MAX_TIE_DATES]}
    if len(fechas) > MAX_TIE_DATES:  # p. ej. lluvia mínima 0,0 repetida en cientos de días
        out["n_fechas"] = len(fechas)
    return out


def rank(rows: Iterable[dict], var: str, top: int = 10, order: Literal["asc", "desc"] = "desc") -> list[dict]:
    """Ranking con competition ranking (1, 2, 2, 4). Desempate por fecha ascendente."""
    pairs = _valid(rows, var)
    reverse = (order == "desc")
    # Orden estable: primero por fecha asc y después por valor. Así los empates quedan por fecha asc.
    pairs.sort(key=lambda p: p[0])
    pairs.sort(key=lambda p: p[1], reverse=reverse)
    result = []
    prev_val = None
    pos = 0
    for i, (fecha, valor) in enumerate(pairs[:top], start=1):
        if valor != prev_val:
            pos, prev_val = i, valor
        result.append({"posicion": pos, "fecha": fecha, "valor": round(valor, 2)})
    return result


def summarize(rows: list[dict], columns: list[str]) -> dict:
    """Agregados por columna numérica: n, min, max, media y suma (solo columnas aditivas)."""
    fechas = sorted(r["fecha"] for r in rows if r.get("fecha"))
    out: dict[str, Any] = {
        "n_dias": len(rows),
        "desde": fechas[0] if fechas else None,
        "hasta": fechas[-1] if fechas else None,
        "metricas": {},
    }
    non_numeric = {"fecha", "n", "dir_racha", "dir_viento_dominante"}
    for col in columns:
        if col in non_numeric or col.startswith("hora_"):
            continue
        pairs = _valid(rows, col)
        vals = [v for _, v in pairs]
        m: dict[str, Any] = {"unidad": UNITS.get(col, ""), "n_dias_con_dato": len(vals)}
        if vals:
            m["min"] = extreme(rows, col, "min")
            m["max"] = extreme(rows, col, "max")
            m["media"] = round(statistics.fmean(vals), 2)
            if col in ADDITIVE_COLUMNS:
                m["suma"] = round(sum(vals), 2)
        out["metricas"][col] = m
    return out


def compute_year_records(rows: list[dict], year: int, today: datetime.date) -> dict:
    """Récords de un año a partir de las filas del dayfile."""
    tmin = _valid(rows, "temp_min")
    tmax = _valid(rows, "temp_max")
    rain = _valid(rows, "lluvia_total")
    t25, t30 = config.WARM_THRESHOLDS
    rain_vals = [v for _, v in rain]
    tmin_vals = [v for _, v in tmin]
    tmax_vals = [v for _, v in tmax]
    return {
        "year": year,
        "parcial": year == today.year,
        "n_dias_con_datos": len(rows),
        "temp_min_absoluta": extreme(rows, "temp_min", "min"),
        "temp_max_absoluta": extreme(rows, "temp_max", "max"),
        "racha_max": extreme(rows, "racha_max", "max"),
        "precipitacion": {
            "acumulada_mm": round(sum(rain_vals), 1) if rain_vals else 0.0,
            "dia_mas_lluvioso": extreme(rows, "lluvia_total", "max"),
            "n_dias_con_dato": len(rain_vals),
            "dias_con_lluvia_gte_0_2": sum(1 for v in rain_vals if v >= 0.2),
        },
        "dias_helada": sum(1 for v in tmin_vals if v <= config.FROST_THRESHOLD),
        "dias_calidos": {
            f"gte_{int(t25)}": sum(1 for v in tmax_vals if v >= t25),
            f"gte_{int(t30)}": sum(1 for v in tmax_vals if v >= t30),
        },
    }


_OPS: dict[str, Any] = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}


_NON_NUMERIC_COLS = {"fecha", "n", "dir_racha", "dir_viento_dominante"}


MAX_LIST_ITEMS = 366


def _numeric_var(variable: str) -> str:
    """Resuelve alias y exige una columna numérica del dayfile."""
    col = resolve_columns([variable])[-1]
    if col in _NON_NUMERIC_COLS or col.startswith("hora_"):
        raise ValueError(f"'{variable}' no es una columna numérica del dayfile")
    return col


def _select_rows(rows: list[dict], start: str = "", end: str = "") -> list[dict]:
    """Filas del dayfile ordenadas por fecha dentro de [start, end] (ambos opcionales)."""
    s = utils._check_date(start, "start") if start else None
    e = utils._check_date(end, "end") if end else None
    if s and e and e < s:
        raise ValueError("end anterior a start")
    sel = [r for r in rows if (s is None or r["_date"] >= s) and (e is None or r["_date"] <= e)]
    return sorted(sel, key=lambda r: r["_date"])


def invalidate_cache() -> None:
    """Vacía la caché en memoria del dayfile: la siguiente consulta lo vuelve a descargar."""
    global _dayfile_cache_time
    _cached_dayfile_by_year.clear()
    _dayfile_cache_time = 0.0
