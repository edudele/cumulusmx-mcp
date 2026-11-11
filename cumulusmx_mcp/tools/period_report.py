"""Tool de informes periódicos (semanal, mensual, anual) en Markdown."""
import datetime
from typing import Literal

from .. import dayrows, reporting, utils
from ..app import READ_ONLY, mcp


@mcp.tool(annotations=READ_ONLY)
async def period_report(period: Literal["week", "month", "year"] = "month", ref_date: str = "") -> str:
    """Informe en Markdown listo para compartir: temperaturas (media, extremos, heladas, días cálidos), lluvia (total, días,
    día más lluvioso, rachas seca y lluviosa), viento, presión, sol y comparación con lo normal de la estación.
    period: 'week' (los 7 días que terminan en ref_date), 'month' (del día 1 de ese mes a ref_date) o 'year' (del 1 de enero
    a ref_date). ref_date (yyyy-mm-dd) por defecto es el último día con datos. Para un mes cerrado usa su último día."""
    rows = await dayrows._all_dayfile()
    if not rows:
        raise ValueError("El dayfile está vacío")
    ref = utils._check_date(ref_date, "ref_date") if ref_date else max(r["_date"] for r in rows)
    if period == "week":
        start, title = ref - datetime.timedelta(days=6), f"Informe semanal ({ref - datetime.timedelta(days=6)} → {ref})"
    elif period == "month":
        start, title = ref.replace(day=1), f"Informe mensual {ref:%Y-%m}" + ("" if ref.day == _month_days(ref) else f" (hasta el {ref.day})")
    else:
        start, title = ref.replace(month=1, day=1), f"Informe anual {ref.year}" + ("" if (ref.month, ref.day) == (12, 31) else f" (hasta el {ref})")
    return reporting.build_period_report(rows, start, ref, title)


def _month_days(d: datetime.date) -> int:
    import calendar
    return calendar.monthrange(d.year, d.month)[1]
