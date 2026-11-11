"""Informes periódicos en Markdown a partir del histórico diario (lógica pura, sin red)."""
import datetime
from typing import Any

from . import analysis, config, dayrows


def _fmt(value: Any, unit: str = "", decimals: int = 1) -> str:
    if value is None:
        return "—"
    text = f"{value:.{decimals}f}" if isinstance(value, float) else str(value)
    return f"{text} {unit}".strip()


def _extreme(item: dict | None, unit: str) -> str:
    return "—" if not item else f"{_fmt(item['valor'], unit)} ({item['fecha']})"


def _anomaly_line(rows: list[dict], col: str, start: datetime.date, end: datetime.date, label: str, unit: str) -> str | None:
    years = sorted({r["_date"].year for r in rows if r["_date"].year != start.year})
    try:
        a = analysis.compute_anomaly(rows, col, start, end, years)
    except ValueError:
        return None
    sign = "+" if a["diferencia"] >= 0 else ""
    pct = f", {sign}{a['diferencia_pct']} %" if a.get("diferencia_pct") is not None else ""
    return (f"- {label}: {_fmt(a['valor'], unit)} frente a {_fmt(a['normal'], unit)} normal "
            f"({sign}{a['diferencia']:g} {unit}{pct}); percentil {a['percentil']} entre {a['anios_comparados']} años.")


def build_period_report(rows: list[dict], start: datetime.date, end: datetime.date, title: str) -> str:
    """Informe en Markdown del periodo [start, end]. Devuelve un aviso si no hay datos."""
    sel = sorted((r for r in rows if start <= r["_date"] <= end), key=lambda r: r["_date"])
    if not sel:
        return f"# {title}\n\nNo hay datos del histórico entre {start} y {end}."
    agg = dayrows._agg(sel)
    expected = (end - start).days + 1
    _, frost = analysis.count_matching(sel, "temp_min", "<=", config.FROST_THRESHOLD)
    _, warm25 = analysis.count_matching(sel, "temp_max", ">=", config.WARM_THRESHOLDS[0])
    _, warm30 = analysis.count_matching(sel, "temp_max", ">=", config.WARM_THRESHOLDS[1])
    dry = analysis.compute_streaks(sel, "lluvia_total", "<", analysis.RAIN_DAY_MM, 2)
    wet = analysis.compute_streaks(sel, "lluvia_total", ">=", analysis.RAIN_DAY_MM, 2)

    lines = [f"# {title}", "", f"Periodo: {start} → {end} ({agg['dias']} de {expected} días con datos). "
             "Unidades de la estación (por defecto °C, mm, km/h, hPa).", ""]
    lines += ["## Temperatura", "",
              f"- Media del periodo: {_fmt(agg['temp_media_del_periodo'], '°C')}",
              f"- Máxima absoluta: {_extreme(agg['temp_max_absoluta'], '°C')}",
              f"- Mínima absoluta: {_extreme(agg['temp_min_absoluta'], '°C')}",
              f"- Días de helada (mín ≤ {config.FROST_THRESHOLD:g}): {len(frost)}",
              f"- Días cálidos: {len(warm25)} con máx ≥ {config.WARM_THRESHOLDS[0]:g}; {len(warm30)} con máx ≥ {config.WARM_THRESHOLDS[1]:g}", ""]
    lines += ["## Lluvia", "",
              f"- Total: {_fmt(agg['lluvia_total'], 'mm')} en {agg['dias_con_lluvia(>=0.2)']} días de lluvia (≥ {analysis.RAIN_DAY_MM:g} mm)",
              f"- Día más lluvioso: {_extreme(agg['dia_mas_lluvioso'], 'mm')}",
              f"- Racha seca más larga: {dry[0]['dias']} días ({dry[0]['desde']} → {dry[0]['hasta']})" if dry
              else "- Racha seca más larga: ninguna de 2 o más días",
              f"- Racha lluviosa más larga: {wet[0]['dias']} días ({wet[0]['desde']} → {wet[0]['hasta']})" if wet
              else "- Racha lluviosa más larga: ninguna de 2 o más días", ""]
    lines += ["## Viento, presión y sol", "",
              f"- Racha máxima: {_extreme(agg['racha_max'], 'km/h')}",
              f"- Presión: máxima {_extreme(agg['pres_max'], 'hPa')}; mínima {_extreme(agg['pres_min'], 'hPa')}",
              f"- Horas de sol: {_fmt(agg['horas_sol_total'], 'h')}; UV máximo {_extreme(agg['uv_max'], '')}", ""]
    comparison = [x for x in (_anomaly_line(rows, "temp_media", start, end, "Temperatura media", "°C"),
                              _anomaly_line(rows, "lluvia_total", start, end, "Lluvia acumulada", "mm")) if x]
    if comparison:
        lines += ["## Frente a lo normal (mismo tramo del calendario en otros años)", ""] + comparison + [""]
    if agg["dias"] < expected:
        lines += ["## Calidad de los datos", "", f"- Faltan {expected - agg['dias']} días con datos en el periodo; "
                  "los totales pueden estar infravalorados.", ""]
    return "\n".join(lines).rstrip() + "\n"
