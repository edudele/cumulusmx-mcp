"""Tools de informes NOAA y resúmenes."""
from typing import Literal


from .. import client
from .. import safety
from .. import utils
from ..app import READ_ONLY, mcp, write_tool


@mcp.tool(annotations=READ_ONLY)
async def noaa_report(period: Literal["noaayear", "noaamonth"], year: int, month: int = 1) -> str:
    """Informe NOAA anual o mensual en texto."""
    if not 2000 <= year <= 2050 or not 1 <= month <= 12:
        raise ValueError("year/month fuera de rango")
    return await client._get(f"/api/reports/{period}", {"year": year, "month": month})


SIMPLE_REPORTS: dict[str, str] = {
    "temperature": "simpleTemperature", "rainfall": "simpleRainfall", "windrun": "simpleWindRun",
    "sunshine": "simpleSunshine", "et": "simpleET", "drydays": "simpleDryDays", "wetdays": "simpleWetDays",
}


@mcp.tool(annotations=READ_ONLY)
async def simple_report(
    report: Literal["temperature", "rainfall", "windrun", "sunshine", "et", "drydays", "wetdays"],
    text: bool = True,
) -> str:
    """Informes resumen de CumulusMX calculados sobre todo el histórico diario (mes a mes por año):
    temperature, rainfall, windrun, sunshine, et (evapotranspiración), drydays y wetdays (días secos/lluviosos
    según el umbral de lluvia de la estación). text=True devuelve texto plano; False, HTML.
    Respeta las unidades y decimales configurados en CumulusMX."""
    if report not in SIMPLE_REPORTS:
        raise ValueError(f"report debe ser uno de {sorted(SIMPLE_REPORTS)}")
    return await client._get(f"/api/reports/{SIMPLE_REPORTS[report]}", {"format": "text"} if text else None)


@write_tool
async def generate_reports(
    report_type: Literal["year", "month", "all"] = "month",
    year: int | None = None,
    month: int | None = None,
) -> str:
    """Genera informes NOAA anuales o mensuales en CumulusMX y los guarda en el servidor.
    report_type='all' genera todos los informes que falten (sin year/month)."""
    if (err := safety._check_write_enabled()):
        return err
    if report_type == "all":
        return await client._get("/api/genreports/all")
    if year is None:
        year = utils._today().year
    if not (2000 <= year <= 2050):
        raise ValueError(f"year fuera de rango (2000..2050): {year}")
    if report_type == "year":
        return await client._get(f"/api/genreports/noaayear?year={year}")
    elif report_type == "month":
        if month is None:
            month = utils._today().month
        if not (1 <= month <= 12):
            raise ValueError(f"month fuera de rango (1..12): {month}")
        return await client._get(f"/api/genreports/noaamonth?year={year}&month={month}")
    else:
        raise ValueError("report_type debe ser 'year', 'month' o 'all'")


@write_tool
async def upload_reports(
    report_type: Literal["year", "month"] = "month",
    year: int | None = None,
    month: int | None = None,
) -> str:
    """Sube informes NOAA anuales o mensuales mediante el método de subida/FTP configurado en CumulusMX."""
    if (err := safety._check_write_enabled()):
        return err
    if year is None:
        year = utils._today().year
    if not (2000 <= year <= 2050):
        raise ValueError(f"year fuera de rango (2000..2050): {year}")
    if report_type == "year":
        return await client._get(f"/api/uploadreport/noaayear?year={year}")
    elif report_type == "month":
        if month is None:
            month = utils._today().month
        if not (1 <= month <= 12):
            raise ValueError(f"month fuera de rango (1..12): {month}")
        return await client._get(f"/api/uploadreport/noaamonth?year={year}&month={month}")
    else:
        raise ValueError("report_type debe ser 'year' o 'month'")
