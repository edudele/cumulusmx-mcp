"""Tools del diario meteorológico."""
import json


from .. import client
from .. import safety
from .. import utils
from ..app import READ_ONLY, mcp, write_tool


@mcp.tool(annotations=READ_ONLY)
async def diary(date: str = "") -> str:
    """Diario meteorológico: si das date (yyyy-mm-dd) devuelve esa entrada; sin fecha, el resumen de días con anotación."""
    if date:
        utils._check_date(date, "date")
        return await client._get("/api/data/diarydata", {"date": date})
    return await client._get("/api/data/diarysummary")


@write_tool
async def edit_diary(
    date: str,
    entry: str = "",
    snow24h: float | None = None,
    snow_depth: float | None = None,
    thunder: bool = False,
    hail: bool = False,
    fog: bool = False,
    gales: bool = False,
    time_str: str = "00:00:00",
) -> str:
    """Crea o actualiza una entrada en el diario meteorológico (diary) de CumulusMX."""
    if (err := safety._check_write_enabled()):
        return err
    d = utils._check_date(date, "date")
    payload = {
        "Date": d.strftime("%Y-%m-%d"),
        "Time": time_str,
        "Entry": entry,
        "Snow24h": snow24h,
        "SnowDepth": snow_depth,
        "Thunder": thunder,
        "Hail": hail,
        "Fog": fog,
        "Gales": gales,
    }
    return await client._post("/api/edit/diarydata", content=json.dumps(payload), headers={"Content-Type": "application/json"})


@write_tool
async def delete_diary(date: str) -> str:
    """Elimina la entrada del diario meteorológico para una fecha específica (yyyy-mm-dd)."""
    if (err := safety._check_write_enabled()):
        return err
    d = utils._check_date(date, "date")
    payload = {"Date": d.strftime("%Y-%m-%d")}
    return await client._post("/api/edit/diarydelete", content=json.dumps(payload), headers={"Content-Type": "application/json"})


@mcp.tool(annotations=READ_ONLY)
async def diary_export() -> str:
    """Exporta todo el diario meteorológico en CSV (texto)."""
    return await client._get("/api/data/diaryexport.txt")


@write_tool
async def diary_upload(csv_content: str) -> str:
    """Importa entradas al diario meteorológico desde texto CSV: la primera línea es la cabecera (se ignora) y cada línea
    siguiente un registro, con el mismo formato que devuelve diary_export."""
    if not csv_content or "\n" not in csv_content.strip():
        raise ValueError("csv_content debe incluir la cabecera y al menos un registro")
    if (err := safety._check_write_enabled()):
        return err
    return await client._post("/api/edit/diaryupload", content=csv_content, headers={"Content-Type": "text/plain; charset=utf-8"})


@write_tool
async def diary_automate(sensor: int) -> str:
    """Configura qué sensor láser de nieve rellena automáticamente el diario (1-4). 0 desactiva la automatización."""
    if not 0 <= sensor <= 4:
        raise ValueError("sensor debe estar entre 0 y 4")
    if (err := safety._check_write_enabled()):
        return err
    return await client._post("/api/edit/diaryautomate", content=str(sensor), headers={"Content-Type": "text/plain"})
