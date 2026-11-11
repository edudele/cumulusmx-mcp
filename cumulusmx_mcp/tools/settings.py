"""Tools de configuración: estación, calibración, alarmas, MySQL, MQTT, NOAA y ajustes genéricos."""
import json
import urllib.parse
from typing import Any, Literal


from .. import client
from .. import safety
from .. import settings_engine
from .. import utils
from ..app import READ_ONLY, mcp, write_tool


@mcp.tool(annotations=READ_ONLY)
async def get_station_settings() -> str:
    """Obtiene la configuración actual de la estación meteorológica en CumulusMX."""
    return await client._get("/api/settings/stationdata.json")


@write_tool
async def update_station_settings(settings: dict[str, Any] | str) -> str:
    """Actualiza la configuración de la estación meteorológica en CumulusMX."""
    if (err := safety._check_write_enabled()):
        return err
    new_data = utils._normalize_settings_payload(settings)
    try:
        current_data = await client._json("/api/settings/stationdata.json")
        if isinstance(current_data, dict):
            merged = utils._deep_merge(dict(current_data), new_data)
        else:
            merged = new_data
    except Exception:
        merged = new_data
    json_str = json.dumps(merged)
    body = "json=" + urllib.parse.quote(json_str)
    return await client._post(
        "/api/setsettings/updatestationconfig.json",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


@mcp.tool(annotations=READ_ONLY)
async def get_calibration_settings() -> str:
    """Obtiene los factores de calibración y offsets actuales de los sensores en CumulusMX."""
    return await client._get("/api/settings/calibrationdata.json")


@write_tool
async def update_calibration_settings(settings: dict[str, Any] | str) -> str:
    """Actualiza los factores y offsets de calibración de los sensores en CumulusMX."""
    if (err := safety._check_write_enabled()):
        return err
    new_data = utils._normalize_settings_payload(settings)
    try:
        current_data = await client._json("/api/settings/calibrationdata.json")
        if isinstance(current_data, dict):
            merged = utils._deep_merge(dict(current_data), new_data)
        else:
            merged = new_data
    except Exception:
        merged = new_data
    json_str = json.dumps(merged)
    body = "json=" + urllib.parse.quote(json_str)
    return await client._post(
        "/api/setsettings/updatecalibrationconfig.json",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


@mcp.tool(annotations=READ_ONLY)
async def get_alarm_settings() -> str:
    """Obtiene la configuración actual de alarmas y umbrales en CumulusMX."""
    res = await client._get("/api/settings/alarms.json")
    if "ERROR HTTP 404" in res:
        return await client._get("/api/settings/alarmdata.json")
    return res


@write_tool
async def update_alarm_settings(settings: dict[str, Any] | str) -> str:
    """Actualiza la configuración de alarmas y umbrales en CumulusMX."""
    if (err := safety._check_write_enabled()):
        return err
    new_data = utils._normalize_settings_payload(settings)
    try:
        current_data = await client._json("/api/settings/alarms.json")
        if isinstance(current_data, dict):
            if "data" in new_data or "units" in new_data or "email" in new_data:
                merged = utils._deep_merge(dict(current_data), new_data)
            else:
                current_copy = dict(current_data)
                current_data_section = current_copy.get("data", {})
                if isinstance(current_data_section, dict):
                    current_copy["data"] = utils._deep_merge(dict(current_data_section), new_data)
                else:
                    current_copy["data"] = new_data
                merged = current_copy
        else:
            merged = new_data if "data" in new_data else {"data": new_data}
    except Exception:
        merged = new_data if "data" in new_data else {"data": new_data}

    return await client._post(
        "/api/setsettings/updatealarmconfig.json",
        content=urllib.parse.quote(json.dumps(merged)),  # CumulusMX aplica UrlDecode al cuerpo
        headers={"Content-Type": "application/json"},
    )


@mcp.tool(annotations=READ_ONLY)
async def get_noaa_settings() -> str:
    """Obtiene la configuración de generación de informes NOAA en CumulusMX."""
    return await client._get("/api/settings/noaadata.json")


@write_tool
async def test_email(
    from_email: str,
    dest_email: str,
    use_html: bool = False,
    use_bcc: bool = False,
) -> str:
    """Envía un correo de prueba desde CumulusMX para verificar la configuración SMTP de alarmas."""
    if (err := safety._check_write_enabled()):
        return err
    payload = json.dumps({
        "fromEmail": from_email,
        "destEmail": dest_email,
        "useHtml": use_html,
        "useBcc": use_bcc,
    })
    return await client._post("/api/setsettings/testemail.json", content=payload, headers={"Content-Type": "application/json"})


@mcp.tool(annotations=READ_ONLY)
async def get_settings(section: settings_engine.SettingsSection) -> str:
    """Lee cualquier sección de configuración de CumulusMX (misma estructura que acepta update_settings):
    program, station, internet (FTP/web), thirdparty (Wunderground, etc.), extrasensors, extrawebfiles, calibration,
    language, noaa, mysql, alarms, useralarms, wizard, customlogs_interval, customlogs_daily, display, httpfiles, mqtt.
    ATENCIÓN: puede incluir contraseñas y claves (FTP, MySQL, MQTT, SMTP...). Con CUMULUS_MASK_SECRETS=true se enmascaran."""
    if section not in settings_engine.SETTINGS_SECTIONS:
        raise ValueError(f"section debe ser una de {sorted(settings_engine.SETTINGS_SECTIONS)}")
    return settings_engine._maybe_mask(await client._get(f"/api/settings/{settings_engine.SETTINGS_SECTIONS[section][0]}"))


@mcp.tool(annotations=READ_ONLY)
async def get_mysql_settings() -> str:
    """Configuración MySQL de CumulusMX: server (host, port, user, pass, database, advanced.sslMode/tlsVers), options
    (updateonedit, bufferonerror), monthly / realtime / dayfile (enabled, table, retención...) y los comandos
    personalizados (customseconds, customminutes, customrollover, customtimed, customstart). Incluye la contraseña salvo
    que CUMULUS_MASK_SECRETS=true."""
    return settings_engine._maybe_mask(await client._get("/api/settings/mysqldata.json"))


@mcp.tool(annotations=READ_ONLY)
async def get_mqtt_settings() -> str:
    """Configuración MQTT de CumulusMX: servidor, puerto, credenciales, TLS, publicación de datos en tiempo real y a
    intervalos (plantillas y topics). Incluye la contraseña salvo que CUMULUS_MASK_SECRETS=true."""
    return settings_engine._maybe_mask(await client._get("/api/settings/mqttdata.json"))


@write_tool
async def update_settings(section: settings_engine.SettingsSection, settings: dict[str, Any] | str, dry_run: bool = False,
                          replace: bool = False) -> str:
    """Modifica CUALQUIER sección de configuración de CumulusMX. Lee la configuración actual, fusiona en profundidad
    solo los campos que indicas (replace=True envía `settings` tal cual, sustituyendo la sección) y la envía con el formato
    que exige cada endpoint. Devuelve los campos modificados (antes/después; secretos enmascarados).
    dry_run=True solo muestra los cambios sin aplicarlos: úsalo para confirmar con el usuario antes de escribir.
    Obtén la estructura con get_settings(section). Si no se puede leer la configuración actual no se escribe nada.
    Algunos cambios requieren reiniciar CumulusMX para tener efecto."""
    return await settings_engine._settings_update(section, settings, dry_run, replace)


@write_tool
async def update_mysql_settings(settings: dict[str, Any] | str, dry_run: bool = False) -> str:
    """Modifica la configuración MySQL (fusión parcial). Ejemplo: {"server": {"host": "db", "port": 3306, "user": "u",
    "pass": "p", "database": "cumulus"}, "monthly": {"enabled": true, "table": "Monthly"}, "dayfile": {"enabled": true},
    "realtime": {"enabled": false}}. Tras activar tablas usa mysql_table(action='create', ...). dry_run=True = vista previa."""
    return await settings_engine._settings_update("mysql", settings, dry_run)


@write_tool
async def update_mqtt_settings(settings: dict[str, Any] | str, dry_run: bool = False) -> str:
    """Modifica la configuración MQTT (fusión parcial). Parte de get_mqtt_settings para ver la estructura exacta
    (server, port, username, password, TLS, topics y plantillas). dry_run=True = vista previa."""
    return await settings_engine._settings_update("mqtt", settings, dry_run)


@write_tool
async def update_noaa_settings(settings: dict[str, Any] | str, dry_run: bool = False) -> str:
    """Modifica la configuración de informes NOAA (nombre del sitio, ciudad, formatos de fichero, umbrales de calor/frío,
    subida...). Fusión parcial a partir de get_noaa_settings. dry_run=True = vista previa."""
    return await settings_engine._settings_update("noaa", settings, dry_run)


@write_tool
async def mysql_table(action: Literal["create", "update"], table: Literal["monthly", "dayfile", "realtime"]) -> str:
    """Crea (create) o actualiza la estructura (update) de las tablas MySQL de CumulusMX: monthly, dayfile o realtime.
    Úsalo después de configurar la conexión con update_mysql_settings."""
    if action not in ("create", "update") or table not in ("monthly", "dayfile", "realtime"):
        raise ValueError("action: create|update; table: monthly|dayfile|realtime")
    if (err := safety._check_write_enabled()):
        return err
    return await client._post(f"/api/setsettings/{action}{table}sql.json")


@mcp.tool(annotations=READ_ONLY)
async def mysql_cache(start: int = 0, length: int = 100, search: str = "") -> str:
    """Comandos SQL pendientes en la caché de reintentos de MySQL (fallos de conexión). Paginado (length máx 500).
    Requiere las credenciales de CumulusMX si la API está protegida (CUMULUS_USER / CUMULUS_PASSWORD)."""
    return await client._get("/api/data/mysqlcache.json", {"draw": 1, "start": max(0, start),
                                                    "length": max(1, min(length, 500)), "search[value]": search[:100]})
