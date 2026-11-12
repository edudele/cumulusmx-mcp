"""Cálculos climáticos puros sobre filas del dayfile (rachas, anomalías, climatología, heladas, grados-día...)."""
import calendar
import collections
import datetime
import math
import statistics
from typing import Any


from . import dayrows
from . import utils


def count_matching(rows: list[dict], col: str, op: str, value: float) -> tuple[int, list[dict]]:
    """(días evaluados con dato, filas que cumplen `col op value`)."""
    fn = dayrows._OPS[op]
    evaluated, hits = 0, []
    for r in rows:
        v = utils._num(r.get(col))
        if v is None:
            continue
        evaluated += 1
        if fn(v, value):
            hits.append(r)
    return evaluated, hits


def compute_streaks(rows: list[dict], col: str, op: str, value: float, min_length: int = 2) -> list[dict]:
    """Rachas de días CONSECUTIVOS que cumplen `col op value`. Un día sin dato o un hueco de fechas corta la racha."""
    fn = dayrows._OPS[op]
    runs: list[dict] = []
    cur: list[datetime.date] = []

    def close() -> None:
        if len(cur) >= min_length:
            runs.append({"dias": len(cur), "desde": str(cur[0]), "hasta": str(cur[-1])})

    for r in sorted(rows, key=lambda x: x["_date"]):
        d = r["_date"]
        v = utils._num(r.get(col))
        if v is not None and fn(v, value):
            if cur and (d - cur[-1]).days == 1:
                cur.append(d)
            else:
                close()
                cur = [d]
        else:
            close()
            cur = []
    close()
    return sorted(runs, key=lambda x: (-x["dias"], x["desde"]))


def _shift_year(d: datetime.date, year: int) -> datetime.date:
    try:
        return d.replace(year=year)
    except ValueError:  # 29 de febrero
        return d.replace(year=year, day=28)


def compute_anomaly(rows: list[dict], col: str, start: datetime.date, end: datetime.date,
                    base_years: list[int], min_coverage: float = 0.8) -> dict:
    """Compara el periodo [start, end] con la misma ventana calendario de `base_years`.
    Columnas aditivas (lluvia, horas de sol...) se SUMAN; el resto se promedia."""
    additive = col in dayrows.ADDITIVE_COLUMNS
    last = max((r["_date"] for r in rows), default=None)
    if last is None or last < start:
        raise ValueError("No hay datos en el dayfile para el periodo indicado")
    eff_end = min(end, last)  # periodo en curso: se compara solo la parte transcurrida
    span = (eff_end - start).days

    def stat(s: datetime.date, e: datetime.date) -> tuple[float | None, int]:
        vals = [v for r in rows if s <= r["_date"] <= e and (v := utils._num(r.get(col))) is not None]
        needed = max(1, math.ceil(min_coverage * ((e - s).days + 1)))
        if len(vals) < needed:
            return None, len(vals)
        return (round(sum(vals), 2) if additive else round(statistics.fmean(vals), 2)), len(vals)

    value, n_days = stat(start, eff_end)
    if value is None:
        raise ValueError(f"Datos insuficientes de '{col}' en el periodo (cobertura mínima {int(min_coverage * 100)} %)")

    by_year: dict[int, float] = {}
    for y in base_years:
        s_y = _shift_year(start, y)
        v, _ = stat(s_y, s_y + datetime.timedelta(days=span))
        if v is not None:
            by_year[y] = v
    if not by_year:
        raise ValueError("No hay años de referencia con datos suficientes para comparar")

    normal = round(statistics.fmean(by_year.values()), 2)
    diff = round(value - normal, 2)
    below = sum(1 for v in by_year.values() if v < value)
    above = sum(1 for v in by_year.values() if v > value)
    out: dict[str, Any] = {
        "variable": col,
        "unidad": dayrows.UNITS.get(col, ""),
        "metodo": "suma" if additive else "media",
        "periodo": {"start": str(start), "end": str(eff_end), "dias_con_dato": n_days,
                    "parcial": eff_end < end},
        "valor": value,
        "normal": normal,
        "diferencia": diff,
        "diferencia_pct": round(100 * diff / normal, 1) if additive and normal else None,
        "percentil": round(100 * below / len(by_year)),
        "posicion_entre_anios": 1 + above,
        "anios_comparados": len(by_year) + 1,
        "por_anio": {str(y): v for y, v in sorted(by_year.items())},
    }
    if len(by_year) < 3:
        out["advertencia"] = "Pocos años de referencia (<3): la 'normal' es poco representativa."
    return out


def check_quality(rows: list[dict], max_items: int = 100) -> dict:
    """Huecos de fechas y valores incoherentes del dayfile (independiente de las unidades)."""
    rows = sorted(rows, key=lambda r: r["_date"])
    if not rows:
        return {"n_dias": 0}
    present = {r["_date"] for r in rows}
    first, last = rows[0]["_date"], rows[-1]["_date"]
    missing = [str(first + datetime.timedelta(days=i)) for i in range((last - first).days + 1)
               if first + datetime.timedelta(days=i) not in present]
    issues: list[dict] = []
    no_temp: list[str] = []

    def flag(r: dict, problem: str) -> None:
        issues.append({"fecha": str(r["_date"]), "problema": problem})

    for r in rows:
        tmin, tmax = utils._num(r.get("temp_min")), utils._num(r.get("temp_max"))
        if tmin is None and tmax is None:
            no_temp.append(str(r["_date"]))
        elif tmin is not None and tmax is not None and tmin > tmax:
            flag(r, f"temp_min ({tmin}) > temp_max ({tmax})")
        hmin, hmax = utils._num(r.get("hum_min")), utils._num(r.get("hum_max"))
        for name, h in (("hum_min", hmin), ("hum_max", hmax)):
            if h is not None and not 0 <= h <= 100:
                flag(r, f"{name} fuera de 0-100 ({h})")
        if hmin is not None and hmax is not None and hmin > hmax:
            flag(r, f"hum_min ({hmin}) > hum_max ({hmax})")
        for name, h in (("hum_min", hmin), ("hum_max", hmax)):
            if h is not None and h == 0:
                flag(r, f"{name} = 0 % (posible fallo del sensor)")
        pmin, pmax = utils._num(r.get("pres_min")), utils._num(r.get("pres_max"))
        for name, p in (("pres_min", pmin), ("pres_max", pmax)):
            if p is not None and p <= 0:
                flag(r, f"{name} = {p:g} (imposible: posible fallo del sensor; se ignora en los resúmenes)")
        if pmin is not None and pmax is not None and pmin > pmax and pmin > 0:
            flag(r, f"pres_min ({pmin}) > pres_max ({pmax})")
        for name in ("lluvia_total", "horas_sol"):
            v = utils._num(r.get(name))
            if v is not None and v < 0:
                flag(r, f"{name} negativo ({v})")

    return {
        "n_dias": len(rows), "desde": str(first), "hasta": str(last),
        "dias_esperados": (last - first).days + 1,
        "dias_faltantes": {"total": len(missing), "fechas": missing[:max_items]},
        "dias_sin_temperatura": {"total": len(no_temp), "fechas": no_temp[:max_items]},
        "valores_incoherentes": {"total": len(issues), "detalle": issues[:max_items]},
        "ok": not missing and not issues and not no_temp,
    }


RAIN_DAY_MM = 0.2  # umbral de "día de lluvia" (el mismo que usa _agg / year_records)


COMPASS_16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _month_stat(rows: list[dict], col: str, days: int, how: str, min_coverage: float) -> float | None:
    """Estadístico mensual de una columna; None si hay menos de `min_coverage` de días con dato."""
    vals = [v for r in rows if (v := utils._num(r.get(col))) is not None]
    if len(vals) < max(1, math.ceil(min_coverage * days)):
        return None
    if how == "sum":
        return sum(vals)
    if how == "mean":
        return statistics.fmean(vals)
    if how == "max":
        return max(vals)
    if how == "min":
        return min(vals)
    if how == "rain_days":
        return float(sum(1 for v in vals if v >= RAIN_DAY_MM))
    raise ValueError(how)


def compute_climatology(rows: list[dict], min_coverage: float = 0.8) -> dict:
    """Normales climáticas por mes del calendario a partir de los meses COMPLETOS (>= min_coverage de días con dato,
    respetando años bisiestos). Para cada mes: medias entre años, extremos absolutos y años récord."""
    by_ym: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        by_ym.setdefault((r["_date"].year, r["_date"].month), []).append(r)

    per_month: dict[int, list[dict]] = {m: [] for m in range(1, 13)}
    for (y, m), rs in sorted(by_ym.items()):
        days = calendar.monthrange(y, m)[1]
        spec = {"temp_media": ("temp_media", "mean"), "temp_max_media": ("temp_max", "mean"),
                "temp_min_media": ("temp_min", "mean"), "temp_max_abs": ("temp_max", "max"),
                "temp_min_abs": ("temp_min", "min"), "lluvia_total": ("lluvia_total", "sum"),
                "dias_lluvia": ("lluvia_total", "rain_days"), "horas_sol": ("horas_sol", "sum"),
                "racha_max": ("racha_max", "max")}
        rec: dict[str, Any] = {"anio": y}
        for key, (col, how) in spec.items():
            rec[key] = _month_stat(rs, col, days, how, min_coverage)
        if any(v is not None for k, v in rec.items() if k != "anio"):
            per_month[m].append(rec)

    def avg(recs: list[dict], key: str) -> float | None:
        vals = [r[key] for r in recs if r[key] is not None]
        return round(statistics.fmean(vals), 2) if vals else None

    def best(recs: list[dict], key: str, fn) -> dict | None:
        cand = [r for r in recs if r[key] is not None]
        if not cand:
            return None
        b = fn(cand, key=lambda r: r[key])
        return {"anio": b["anio"], "valor": round(b[key], 2)}

    out: dict[str, Any] = {}
    for m, recs in per_month.items():
        if not recs:
            continue
        out[f"{m:02d}"] = {
            "n_anios": len(recs),
            "temp_media": avg(recs, "temp_media"), "temp_max_media": avg(recs, "temp_max_media"),
            "temp_min_media": avg(recs, "temp_min_media"),
            "lluvia_total_media": avg(recs, "lluvia_total"), "dias_lluvia_media": avg(recs, "dias_lluvia"),
            "horas_sol_media": avg(recs, "horas_sol"),
            "temp_max_absoluta": best(recs, "temp_max_abs", max), "temp_min_absoluta": best(recs, "temp_min_abs", min),
            "racha_max": best(recs, "racha_max", max),
            "mes_mas_calido": best(recs, "temp_media", max), "mes_mas_frio": best(recs, "temp_media", min),
            "mes_mas_lluvioso": best(recs, "lluvia_total", max), "mes_mas_seco": best(recs, "lluvia_total", min),
        }
    return out


def compute_frost_dates(rows: list[dict], year: int, threshold: float, hemisphere: str = "north",
                        today: datetime.date | None = None) -> dict:
    """Última helada de primavera y primera de otoño de un año (helada = temp_min <= threshold).
    Hemisferio norte: primavera = ene-jun, otoño = jul-dic. Hemisferio sur: primera y última helada del año natural."""
    yr_rows = [r for r in rows if r["_date"].year == year]
    valid = [(r["_date"], t) for r in yr_rows if (t := utils._num(r.get("temp_min"))) is not None]
    frost = sorted(d for d, t in valid if t <= threshold)
    if hemisphere == "north":
        spring = [d for d in frost if d.month <= 6]
        autumn = [d for d in frost if d.month >= 7]
        last_spring = spring[-1] if spring else None
        first_autumn = autumn[0] if autumn else None
    else:
        last_spring = frost[-1] if frost else None
        first_autumn = frost[0] if frost else None
    free = None
    if hemisphere == "north" and last_spring and first_autumn:
        free = (first_autumn - last_spring).days - 1
    year_days = 366 if calendar.isleap(year) else 365
    partial = len(valid) < 0.9 * year_days or (today is not None and year == today.year)
    return {
        "anio": year, "n_dias_con_dato": len(valid), "dias_helada": len(frost), "parcial": partial,
        "ultima_helada_primavera": str(last_spring) if last_spring else None,
        "primera_helada_otono": str(first_autumn) if first_autumn else None,
        "periodo_sin_heladas_dias": free,
    }


def _mean_month_day(dates: list[str]) -> str | None:
    """Fecha media (MM-DD) de una lista de fechas ISO, promediando el día del año."""
    if not dates:
        return None
    doys = [datetime.date.fromisoformat(d).timetuple().tm_yday for d in dates]
    ref = datetime.date(2001, 1, 1) + datetime.timedelta(days=round(statistics.fmean(doys)) - 1)
    return ref.strftime("%m-%d")


def compute_degree_days(rows: list[dict], base: float, upper: float | None = None,
                        method: str = "average") -> list[tuple[datetime.date, float]]:
    """Grados-día de crecimiento por día. 'average': max(0, (tmax+tmin)/2 - base).
    'modified': antes de promediar, tmin se sube hasta `base` y tmax se limita a `upper` (método habitual en agronomía)."""
    out: list[tuple[datetime.date, float]] = []
    for r in sorted(rows, key=lambda x: x["_date"]):
        tmin, tmax = utils._num(r.get("temp_min")), utils._num(r.get("temp_max"))
        if tmin is None or tmax is None:
            continue
        if method == "modified":
            tmax_c = max(min(tmax, upper) if upper is not None else tmax, base)
            tmin_c = max(tmin, base)
            dd = (tmax_c + tmin_c) / 2 - base
        else:
            dd = (tmax + tmin) / 2 - base
        out.append((r["_date"], max(0.0, dd)))
    return out


def compass_sector(degrees: float) -> str:
    return COMPASS_16[int((degrees % 360) / 22.5 + 0.5) % 16]


def compute_wind_rose(rows: list[dict]) -> dict:
    """Frecuencia por sector (16 rumbos) de la dirección dominante diaria y racha máxima por sector de la racha."""
    dom: collections.Counter = collections.Counter()
    gust_n: collections.Counter = collections.Counter()
    gust_max: dict[str, float] = {}
    for r in rows:
        d = utils._num(r.get("dir_viento_dominante"))
        if d is not None:
            dom[compass_sector(d)] += 1
        gd, gv = utils._num(r.get("dir_racha")), utils._num(r.get("racha_max"))
        if gd is not None and gv is not None:
            sec = compass_sector(gd)
            gust_n[sec] += 1
            gust_max[sec] = max(gust_max.get(sec, gv), gv)
    total = sum(dom.values())
    sectors = [{"sector": s, "dias": dom[s], "pct": round(100 * dom[s] / total, 1) if total else 0.0}
               for s in COMPASS_16]
    top = max(COMPASS_16, key=lambda s: dom[s]) if total else None
    return {
        "dias_con_direccion": total, "sector_mas_frecuente": top, "sectores": sectors,
        "rachas_por_sector": [{"sector": s, "dias_con_racha_maxima_en_este_sector": gust_n[s],
                               "racha_max": round(gust_max[s], 1)} for s in COMPASS_16 if gust_n[s]],
    }


def percentile_in_history(rows: list[dict], col: str, value: float, month: int, exclude: datetime.date,
                          min_samples: int = 5) -> dict | None:
    """Posición de `value` entre los valores del mismo mes del calendario en el resto del histórico."""
    vals = [v for r in rows if r["_date"].month == month and r["_date"] != exclude
            and (v := utils._num(r.get(col))) is not None]
    if len(vals) < min_samples:
        return None
    return {"valor": value, "percentil": round(100 * sum(1 for v in vals if v < value) / len(vals)),
            "posicion": 1 + sum(1 for v in vals if v > value), "de": len(vals) + 1}


def _ongoing_run(rows: list[dict], col: str, op: str, value: float, last: datetime.date) -> int:
    """Días consecutivos, hasta el último día con datos, que cumplen la condición (0 si no hay racha en curso)."""
    for run in compute_streaks(rows, col, op, value, min_length=1):
        if run["hasta"] == str(last):
            return run["dias"]
    return 0
