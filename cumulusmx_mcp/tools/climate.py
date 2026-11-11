"""Tools de historial y análisis climático."""
import asyncio
import json
from typing import Any, Literal


from .. import analysis
from .. import client
from .. import config
from .. import dayrows
from .. import utils
from ..app import READ_ONLY, mcp


@mcp.tool(annotations=READ_ONLY)
async def dayfile(start: int = 0, length: int = 31, search: str = "") -> str:
    """Resumen diario histórico (dayfile), paginado. length máx. 500."""
    length = max(1, min(length, 500))
    return await client._get("/api/data/dayfile", {"draw": 1, "start": max(start, 0), "length": length,
                                            "search[value]": search[:100]})


@mcp.tool(annotations=READ_ONLY)
async def dayfile_range(start: str, end: str, columns: list[str] | None = None, max_rows: int = 400, summary_only: bool = False) -> str:
    """Resumen diario (dayfile) entre dos fechas yyyy-mm-dd, con nombres de columna legibles y soporte de alias.
    columns: lista opcional de columnas o alias (ej. ['date', 'MinTemp', 'MaxTemp', 'Rain', 'Wind', 'temp_min']).
    max_rows: máximo de filas a devolver si summary_only=False (máx 400).
    summary_only: si es True, devuelve solo el resumen estadístico agregado de las columnas seleccionadas en vez de las filas individuales."""
    s, e = utils._check_date(start, "start"), utils._check_date(end, "end")
    if e < s:
        raise ValueError("end anterior a start")
    cols = dayrows.resolve_columns(columns)
    all_rows = await dayrows._all_dayfile()
    sel = [r for r in all_rows if s <= r["_date"] <= e]
    if summary_only:
        return json.dumps(dayrows.summarize(sel, cols), ensure_ascii=False)
    limited = sel[: max(1, min(max_rows, 400))]
    return json.dumps([{c: r.get(c) for c in cols} for r in limited], ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def climate_summary(start: str, end: str) -> str:
    """Estadísticas agregadas del periodo [start, end] (yyyy-mm-dd) calculadas del histórico diario:
    temperatura máxima/mínima absoluta con fecha, media, lluvia total, días de lluvia, día más lluvioso,
    racha máxima, presión extrema, horas de sol, UV y radiación máximos."""
    s, e = utils._check_date(start, "start"), utils._check_date(end, "end")
    if e < s:
        raise ValueError("end anterior a start")
    rows = [r for r in await dayrows._all_dayfile() if s <= r["_date"] <= e]
    return json.dumps(dayrows._agg(rows), ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def monthly_summary(year: int) -> str:
    """Resumen mes a mes de un año (temperaturas extremas y media, lluvia, días de lluvia, racha, horas de sol)."""
    if not 2000 <= year <= 2050:
        raise ValueError("year fuera de rango")
    allrows = [r for r in await dayrows._all_dayfile() if r["_date"].year == year]
    out = {}
    for m in range(1, 13):
        a = dayrows._agg([r for r in allrows if r["_date"].month == m])
        if a["dias"]:
            out[f"{year}-{m:02d}"] = a
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def compare_periods(start_a: str, end_a: str, start_b: str, end_b: str) -> str:
    """Compara dos periodos (yyyy-mm-dd) con las mismas estadísticas que climate_summary
    (p.ej. este octubre vs. octubre del año pasado)."""
    rows = await dayrows._all_dayfile()
    res = {}
    for name, (s, e) in {"A": (start_a, end_a), "B": (start_b, end_b)}.items():
        sd, ed = utils._check_date(s, f"start_{name}"), utils._check_date(e, f"end_{name}")
        res[name] = dayrows._agg([r for r in rows if sd <= r["_date"] <= ed])
    return json.dumps(res, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def rankings(
    variable: Literal["temp_min", "temp_max", "lluvia_total", "racha_max", "temp_media", "horas_sol"],
    top: int = 10,
    order: Literal["asc", "desc"] = "desc",
    start_year: int | None = None,
    end_year: int | None = None,
) -> str:
    """Ranking histórico de una variable meteorológica calculada sobre el dayfile.
    variable: 'temp_min', 'temp_max', 'lluvia_total', 'racha_max', 'temp_media', 'horas_sol'.
    top: número de posiciones a devolver (por defecto 10, máx 100).
    order: 'asc' o 'desc' (por defecto 'desc').
    start_year: año de inicio (por defecto CUMULUS_DATA_START_YEAR, 2023 si no se configura).
    end_year: año final (por defecto el año actual)."""
    valid_vars = ("temp_min", "temp_max", "lluvia_total", "racha_max", "temp_media", "horas_sol")
    if variable not in valid_vars:
        raise ValueError(f"variable '{variable}' no válida. Opciones: {list(valid_vars)}")
    if order not in ("asc", "desc"):
        raise ValueError("order debe ser 'asc' o 'desc'")
    current_year = utils._today().year
    start_year = start_year if start_year is not None else config.DATA_START_YEAR
    end_y = end_year if end_year is not None else current_year
    if start_year > end_y:
        raise ValueError("start_year no puede ser posterior a end_year")
    top_clamped = max(1, min(top, 100))
    rows = await dayrows._get_dayfile_rows(start_year=start_year, end_year=end_y)
    ranked = dayrows.rank(rows, variable, top=top_clamped, order=order)
    res = {
        "variable": variable,
        "unidad": dayrows.UNITS.get(variable, ""),
        "orden": order,
        "periodo": {"start_year": start_year, "end_year": end_y},
        "top": ranked,
    }
    return json.dumps(res, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def year_records(year: int) -> str:
    """Récords y estadísticas anuales a partir del dayfile del año indicado.
    Devuelve JSON con las temperaturas extremas absolutas, racha máxima, precipitación acumulada,
    día más lluvioso, días de helada y días cálidos."""
    if not 2000 <= year <= 2050:
        raise ValueError("year fuera de rango (2000-2050)")
    all_rows = await dayrows._all_dayfile()
    rows = [r for r in all_rows if r["_date"].year == year]
    if not rows:
        raise ValueError(f"No hay datos registrados en el dayfile para el año {year}")
    res = dayrows.compute_year_records(rows, year, utils._today())
    return json.dumps(res, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def count_days(variable: str, op: Literal[">", ">=", "<", "<=", "=="], value: float,
                     start: str = "", end: str = "", list_days: bool = False) -> str:
    """Cuenta los días del histórico que cumplen una condición sobre una columna del dayfile, con desglose por año.
    Ej.: variable='temp_max', op='>=', value=35 -> días de calor extremo; 'rain' '>=' 0.2 -> días de lluvia;
    'temp_min' '<=' 0 -> días de helada. variable acepta nombres o alias (ver dayfile_range).
    start/end opcionales (yyyy-mm-dd; por defecto todo el histórico). list_days=True lista las fechas (máx 366).
    Compara en las unidades de la estación."""
    col = dayrows._numeric_var(variable)
    rows = dayrows._select_rows(await dayrows._all_dayfile(), start, end)
    evaluated, hits = analysis.count_matching(rows, col, op, value)
    by_year: dict[str, int] = {}
    for r in hits:
        by_year[str(r["_date"].year)] = by_year.get(str(r["_date"].year), 0) + 1
    out: dict[str, Any] = {
        "condicion": f"{col} {op} {value:g}", "unidad": dayrows.UNITS.get(col, ""),
        "periodo": {"desde": str(rows[0]["_date"]) if rows else None, "hasta": str(rows[-1]["_date"]) if rows else None},
        "dias_evaluados": evaluated, "dias_que_cumplen": len(hits),
        "por_anio": dict(sorted(by_year.items())),
    }
    if list_days:
        out["fechas"] = [str(r["_date"]) for r in hits[:dayrows.MAX_LIST_ITEMS]]
        out["lista_truncada"] = len(hits) > dayrows.MAX_LIST_ITEMS
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def find_streaks(variable: str, op: Literal[">", ">=", "<", "<=", "=="], value: float,
                       start: str = "", end: str = "", min_length: int = 2, top: int = 5) -> str:
    """Rachas más largas de días CONSECUTIVOS que cumplen una condición (un día sin dato corta la racha).
    Ej.: sequía = variable='rain', op='<', value=0.2; ola de calor = 'temp_max' '>=' 35; helada = 'temp_min' '<=' 0.
    top: nº de rachas a devolver (1-50). min_length: longitud mínima en días.
    Indica también si hay una racha en curso (termina en el último día con datos)."""
    col = dayrows._numeric_var(variable)
    rows = dayrows._select_rows(await dayrows._all_dayfile(), start, end)
    runs = analysis.compute_streaks(rows, col, op, value, max(1, min_length))
    last_day = str(rows[-1]["_date"]) if rows else None
    out = {
        "condicion": f"{col} {op} {value:g}", "unidad": dayrows.UNITS.get(col, ""),
        "periodo": {"desde": str(rows[0]["_date"]) if rows else None, "hasta": last_day},
        "n_rachas": len(runs),
        "rachas": runs[:max(1, min(top, 50))],
        "racha_en_curso": next((r for r in runs if r["hasta"] == last_day), None),
    }
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def anomaly(start: str, end: str, variable: str = "temp_media",
                  baseline_start_year: int | None = None, baseline_end_year: int | None = None) -> str:
    """Compara un periodo con la climatología propia de la estación: el mismo tramo del calendario en los demás años.
    Responde a '¿este mes fue más cálido/seco de lo normal?'. variable: nombre o alias (por defecto temp_media);
    lluvia y horas de sol se SUMAN, el resto se promedia. Devuelve valor, normal, diferencia (y % en acumulados),
    percentil, posición entre años y valores por año. Si el periodo está en curso compara solo la parte transcurrida.
    baseline_start_year por defecto = CUMULUS_DATA_START_YEAR. Años con <80 % de datos en la ventana se descartan."""
    s, e = utils._check_date(start, "start"), utils._check_date(end, "end")
    if e < s:
        raise ValueError("end anterior a start")
    col = dayrows._numeric_var(variable)
    rows = await dayrows._all_dayfile()
    lo = baseline_start_year if baseline_start_year is not None else config.DATA_START_YEAR
    hi = baseline_end_year if baseline_end_year is not None else 2050
    years = sorted({r["_date"].year for r in rows if lo <= r["_date"].year <= hi and r["_date"].year != s.year})
    return json.dumps(analysis.compute_anomaly(rows, col, s, e, years), ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def data_quality(start: str = "", end: str = "", max_items: int = 100) -> str:
    """Revisa la calidad del histórico diario: días sin registro, días sin temperatura y valores incoherentes
    (mín > máx, humedad fuera de 0-100, lluvia o sol negativos). Útil para detectar caídas de la estación o
    sensores averiados antes de fiarse de los récords. max_items limita las listas (1-366)."""
    rows = dayrows._select_rows(await dayrows._all_dayfile(), start, end)
    return json.dumps(analysis.check_quality(rows, max(1, min(max_items, dayrows.MAX_LIST_ITEMS))), ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def climatology(start_year: int | None = None, end_year: int | None = None) -> str:
    """Normales climáticas de la estación por mes del calendario (enero-diciembre), calculadas con los meses completos
    del histórico (>= 80 % de días con dato): temperatura media/máx/mín, lluvia total y días de lluvia medios, horas de
    sol, extremos absolutos y el año más cálido/frío/lluvioso/seco de cada mes. Sirve para preguntas como
    '¿cuánto llueve normalmente en abril?' y como referencia de anomaly. start_year por defecto = CUMULUS_DATA_START_YEAR."""
    lo = start_year if start_year is not None else config.DATA_START_YEAR
    hi = end_year if end_year is not None else utils._today().year
    if lo > hi:
        raise ValueError("start_year no puede ser posterior a end_year")
    rows = [r for r in await dayrows._all_dayfile() if lo <= r["_date"].year <= hi]
    if not rows:
        raise ValueError(f"No hay datos en el dayfile entre {lo} y {hi}")
    out = {"periodo": {"start_year": lo, "end_year": hi}, "anios_con_datos": sorted({r["_date"].year for r in rows}),
           "meses": analysis.compute_climatology(rows),
           "nota": "Solo cuentan los meses con >= 80 % de días con dato; unidades de la estación."}
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def frost_dates(threshold: float | None = None, start_year: int | None = None, end_year: int | None = None,
                      hemisphere: Literal["north", "south"] = "north") -> str:
    """Fechas de heladas por año: última helada de primavera, primera de otoño, días de helada y periodo sin heladas
    (días entre ambas, hemisferio norte). Helada = temp_min <= threshold (por defecto CUMULUS_FROST_THRESHOLD, 0 °C).
    Incluye fecha media (MM-DD) de las dos, calculada solo con años completos: útil para planificar siembras y
    trasplantes. Los años incompletos se marcan 'parcial'. Hemisferio norte: primavera = ene-jun, otoño = jul-dic."""
    thr = threshold if threshold is not None else config.FROST_THRESHOLD
    lo = start_year if start_year is not None else config.DATA_START_YEAR
    hi = end_year if end_year is not None else utils._today().year
    if lo > hi:
        raise ValueError("start_year no puede ser posterior a end_year")
    rows = await dayrows._all_dayfile()
    years = sorted({r["_date"].year for r in rows if lo <= r["_date"].year <= hi})
    if not years:
        raise ValueError(f"No hay datos en el dayfile entre {lo} y {hi}")
    per_year = [analysis.compute_frost_dates(rows, y, thr, hemisphere, utils._today()) for y in years]
    complete = [y for y in per_year if not y["parcial"]]
    out = {
        "umbral": thr, "hemisferio": hemisphere, "anios": per_year,
        "fecha_media": {
            "ultima_helada_primavera": analysis._mean_month_day([y["ultima_helada_primavera"] for y in complete
                                                        if y["ultima_helada_primavera"]]),
            "primera_helada_otono": analysis._mean_month_day([y["primera_helada_otono"] for y in complete
                                                     if y["primera_helada_otono"]]),
            "anios_completos": len(complete),
        },
    }
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def degree_days(start: str = "", end: str = "", base: float = 10.0, upper: float | None = None,
                      method: Literal["average", "modified"] = "average") -> str:
    """Grados-día de crecimiento (GDD) acumulados con temp_min/temp_max diarias, en las unidades de la estación.
    base: temperatura base del cultivo (10 por defecto; 0, 5, 10... según cultivo). method='average': max(0, media - base);
    'modified': tmin se sube a base y tmax se limita a `upper` antes de promediar (recomendado con upper, p. ej. 30).
    Por defecto desde el 1 de enero del año actual hasta el último día con datos. Devuelve total, por mes y por año.
    (CumulusMX ya trae grados-día de calefacción/refrigeración en las columnas grados_dia_calef / grados_dia_refr.)"""
    if upper is not None and upper <= base:
        raise ValueError("upper debe ser mayor que base")
    s = start or f"{utils._today().year}-01-01"
    rows = dayrows._select_rows(await dayrows._all_dayfile(), s, end)
    series = analysis.compute_degree_days(rows, base, upper, method)
    by_month: dict[str, float] = {}
    by_year: dict[str, float] = {}
    for d, v in series:
        by_month[f"{d.year}-{d.month:02d}"] = by_month.get(f"{d.year}-{d.month:02d}", 0.0) + v
        by_year[str(d.year)] = by_year.get(str(d.year), 0.0) + v
    out = {
        "base": base, "techo": upper, "metodo": method,
        "periodo": {"desde": str(series[0][0]) if series else None, "hasta": str(series[-1][0]) if series else None},
        "dias_evaluados": len(series), "total": round(sum(v for _, v in series), 1),
        "por_mes": {k: round(v, 1) for k, v in by_month.items()},
        "por_anio": {k: round(v, 1) for k, v in by_year.items()},
    }
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def wind_rose(start: str = "", end: str = "") -> str:
    """Rosa de los vientos del histórico diario (16 rumbos): cuántos días domina cada dirección (dir_viento_dominante) y la
    racha máxima registrada con cada dirección de racha (dir_racha). start/end opcionales (yyyy-mm-dd; por defecto todo).
    Un rumbo de 0 puede significar calma en CumulusMX: interprétalo con cautela."""
    rows = dayrows._select_rows(await dayrows._all_dayfile(), start, end)
    out = analysis.compute_wind_rose(rows)
    out["periodo"] = {"desde": str(rows[0]["_date"]) if rows else None, "hasta": str(rows[-1]["_date"]) if rows else None}
    return json.dumps(out, ensure_ascii=False)


BRIEFING_TAGS = ["temp", "feelslike", "dew", "hum", "press", "presstrend", "wlatest", "wgust", "currentwdir", "rrate",
                 "rfall", "SolarRad", "UV", "tempTH", "TtempTH", "tempTL", "TtempTL", "DataStopped", "time", "date",
                 "tempunit", "pressunit", "windunit", "rainunit", "forecast", "cloudbasevalue", "cloudbaseunit"]


@mcp.tool(annotations=READ_ONLY)
async def daily_briefing() -> str:
    """Resumen del día en una sola llamada: condiciones ahora y extremos de hoy; AYER en contexto (percentil de su Tmax,
    Tmín y lluvia frente al mismo mes de otros años); mes en curso frente a lo normal (temperatura y lluvia acumulada);
    y rachas en curso (días sin lluvia, con Tmax >= 30 y de helada). Combina datos en vivo con el histórico diario,
    que llega hasta el último día cerrado ('datos_hasta')."""
    tags_text, rows_or_err = await asyncio.gather(client._get(client._tags_path(BRIEFING_TAGS)), dayrows._all_dayfile(), return_exceptions=True)
    out: dict[str, Any] = {"fecha_servidor": str(utils._today())}
    try:
        tags = json.loads(tags_text) if isinstance(tags_text, str) else {}
    except ValueError:
        tags = {}
        out["aviso_ahora"] = str(tags_text)[:200]
    out["ahora"] = {k: (n if (n := utils._num(v)) is not None else utils._clean(v)) if isinstance(v, str) else v
                    for k, v in tags.items()}
    if isinstance(rows_or_err, Exception) or not rows_or_err:
        out["aviso_historico"] = f"Sin histórico disponible: {rows_or_err}" if isinstance(rows_or_err, Exception) \
            else "El dayfile está vacío"
        return json.dumps(out, ensure_ascii=False)

    rows = rows_or_err
    today = utils._today()
    last = max(r["_date"] for r in rows)
    out["datos_hasta"] = str(last)
    if (today - last).days > 2:
        out["aviso_historico"] = f"El histórico llega solo hasta {last}: puede estar desactualizado."

    last_row = next(r for r in rows if r["_date"] == last)
    context: dict[str, Any] = {}
    for col in ("temp_max", "temp_min", "lluvia_total"):
        v = utils._num(last_row.get(col))
        context[col] = analysis.percentile_in_history(rows, col, v, last.month, last) if v is not None else None
    out["ultimo_dia_cerrado_en_contexto"] = {"fecha": str(last), **context}

    month_start = today.replace(day=1)
    years = sorted({r["_date"].year for r in rows if r["_date"].year != today.year and r["_date"].year >= config.DATA_START_YEAR})
    month: dict[str, Any] = {}
    for col in ("temp_media", "lluvia_total"):
        try:
            a = analysis.compute_anomaly(rows, col, month_start, today, years)
            month[col] = {k: a[k] for k in ("valor", "normal", "diferencia", "diferencia_pct", "percentil",
                                            "posicion_entre_anios", "anios_comparados") if k in a}
        except ValueError as e:
            month[col] = {"aviso": str(e)}
    out["mes_en_curso"] = month

    stale = (today - last).days > 2
    out["rachas_en_curso"] = None if stale else {
        "dias_sin_lluvia": analysis._ongoing_run(rows, "lluvia_total", "<", analysis.RAIN_DAY_MM, last),
        "dias_con_tmax_ge_30": analysis._ongoing_run(rows, "temp_max", ">=", config.WARM_THRESHOLDS[1], last),
        "dias_de_helada": analysis._ongoing_run(rows, "temp_min", "<=", config.FROST_THRESHOLD, last),
    }
    return json.dumps(out, ensure_ascii=False)
