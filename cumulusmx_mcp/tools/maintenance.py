"""Tools de mantenimiento de CumulusMX y edición de valores en vivo."""
import json


from .. import backups, client
from .. import dayrows
from .. import safety
from ..app import READ_ONLY, mcp, write_tool


ALL_ALARMS = (
    "DataStopped", "BatteryLow", "Sensor", "Spike", "WindHigh", "WindGust",
    "RainRate", "Rainfall", "PressUp", "PressDown", "PressHigh", "PressLow",
    "TempUp", "TempDown", "TempLow", "TempHigh", "Upgrade", "Firmware",
    "Thirdparty", "MySQL", "IsRaining", "Record", "FTP", "Error",
)


@write_tool
async def reloaddayfile() -> str:
    """Fuerza a CumulusMX a recargar dayfile.txt desde el disco e invalida la caché local en memoria."""
    if (err := safety._check_write_enabled()):
        return err
    async with dayrows._dayfile_lock:
        dayrows.invalidate_cache()
    res = await client._get("/api/utils/reloaddayfile")
    return f"Caché local de dayfile invalidada. CumulusMX: {res}"


@write_tool
async def clear_alarms(alarm: str = "all") -> str:
    """Restablece una alarma específica (p.ej. 'BatteryLow', 'AlarmSensor') o todas las alarmas en CumulusMX."""
    if (err := safety._check_write_enabled()):
        return err
    target = alarm.strip()
    if target.lower() == "all":
        results = []
        for name in ALL_ALARMS:
            alarm_id = f"Alarm{name}"
            r = await client._post("/api/utils/clearalarm.txt", content=alarm_id.encode("utf-8"), headers={"Content-Type": "text/plain"})
            results.append(f"{name}: {r.strip()}")
        return "Alarmas restablecidas:\n" + "\n".join(results)
    else:
        alarm_id = target if target.startswith("Alarm") else f"Alarm{target}"
        return await client._post("/api/utils/clearalarm.txt", content=alarm_id.encode("utf-8"), headers={"Content-Type": "text/plain"})


@write_tool
async def clear_error_log() -> str:
    """Borra el registro de errores acumulado en CumulusMX."""
    if (err := safety._check_write_enabled()):
        return err
    return await client._post("/api/utils/clearerrorlog.json")


@write_tool
async def trigger_ftp(
    dailygraphs: bool = False,
    noaa: bool = False,
    graphs: bool = False,
    logfiles: bool = False,
) -> str:
    """Dispara inmediatamente una subida o copia local por FTP/Web en CumulusMX."""
    if (err := safety._check_write_enabled()):
        return err
    payload = json.dumps({
        "dailygraphs": dailygraphs,
        "noaa": noaa,
        "graphs": graphs,
        "logfiles": logfiles,
    })
    return await client._post("/api/utils/ftpnow.json", content=payload, headers={"Content-Type": "application/json"})


@write_tool
async def purgemysql() -> str:
    """Purga la cola de comandos pendientes/fallidos y la caché MySQL en CumulusMX."""
    if (err := safety._check_write_enabled()):
        return err
    return await client._get("/api/utils/purgemysql")


@mcp.tool(annotations=READ_ONLY)
async def get_error_log() -> str:
    """Obtiene el registro de errores y avisos del sistema en CumulusMX."""
    return await client._get("/api/data/errorlog.json")


@write_tool
async def edit_current_conditions(text: str) -> str:
    """Actualiza el texto descriptivo de las condiciones meteorológicas actuales de la estación."""
    if (err := safety._check_write_enabled()):
        return err
    return await client._post(
        "/api/edit/currcond",
        content=text.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )


RAIN_FIELDS = ("raintoday", "midnightrain", "rainratetoday")


async def _edit_rain_today(raintoday: float, midnightrain: float | None, rainratetoday: float | None) -> str:
    """Implementación de edit_rain_today (también la usa restore_backup).

    CumulusMX exige TRES campos (raintoday, midnightrain y rainratetoday): si falta uno solo anota el error en su log, no
    cambia nada y responde 200 con los valores sin tocar. Por eso se leen los actuales, se envían los tres y se comprueba
    en la respuesta que el valor nuevo se aplicó."""
    try:
        current = await client._json("/api/edit/raintodayeditdata.json")
        old = {k: str(current[k]) for k in RAIN_FIELDS}
    except (ValueError, KeyError) as exc:
        return f"ERROR: no se pudo leer la lluvia actual de CumulusMX; no se modifica nada ({exc})"
    new = {
        "raintoday": float(raintoday),
        "midnightrain": float(old["midnightrain"]) if midnightrain is None else float(midnightrain),
        "rainratetoday": float(old["rainratetoday"]) if rainratetoday is None else float(rainratetoday),
    }
    backup_id = backups.save("rain", "hoy", {**old, "accion": "Edit"}, "edit_rain_today")
    response = await client._post("/api/edit/raintoday", data={k: f"{v:.3f}" for k, v in new.items()})
    try:
        applied = abs(float(json.loads(response)["raintoday"]) - new["raintoday"]) <= 0.051
    except (ValueError, KeyError, TypeError):
        applied = False
    if not applied:
        return f"ERROR: CumulusMX no aplicó el cambio de la lluvia de hoy (respuesta: {response[:200]})"
    result = {"respuesta": response, "copia_id": backup_id}
    if backups.enabled() and backup_id is None:
        result["aviso"] = "No se pudo guardar la copia previa de la lluvia (ver el log del servidor)."
    return json.dumps(result, ensure_ascii=False)


@write_tool
async def edit_rain_today(raintoday: float, midnightrain: float | None = None, rainratetoday: float | None = None) -> str:
    """Modifica la lluvia acumulada de hoy y, si se indican, la lluvia desde medianoche y la intensidad máxima de lluvia de
    hoy (por defecto se conservan los valores actuales). Comprueba que CumulusMX aplicó el cambio y guarda antes los valores
    anteriores (copia_id; restaurable con restore_backup)."""
    return await _edit_rain_today(raintoday, midnightrain, rainratetoday)