"""Recursos MCP (`weather://...`)."""
import json


from . import client
from .tools import observations
from .tools import records
from .app import mcp


@mcp.resource("weather://current")
async def current_weather_resource() -> str:
    """Condiciones meteorológicas actuales en tiempo real."""
    return await client._get("/api/data/currentdata")


@mcp.resource("weather://today")
async def today_weather_resource() -> str:
    """Resumen de datos y extremos de hoy y ayer."""
    return await observations.today_all(strip_html=True)


@mcp.resource("weather://station")
async def station_info_resource() -> str:
    """Información general, versión y unidades de la estación meteorológica."""
    ver = await client._get("/api/info/version.json")
    units = await client._get("/api/info/units.json")
    return json.dumps({
        "version": json.loads(ver) if ver.startswith("{") else ver,
        "units": json.loads(units) if units.startswith("{") else units,
    }, ensure_ascii=False)


@mcp.resource("weather://records/alltime")
async def records_alltime_resource() -> str:
    """Récords meteorológicos históricos absolutos de la estación."""
    return await records.records_all(scope="alltime", strip_html=True)
