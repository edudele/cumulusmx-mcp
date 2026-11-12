"""Motor genérico de ajustes: lectura-fusión-escritura con vista previa, enmascarado de secretos y formato de cuerpo."""
import copy
import json
import urllib.parse
from typing import Any, Literal


from . import backups, client, safety
from . import config
from . import utils


FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}


JSON_HEADERS = {"Content-Type": "application/json"}


# sección -> (GET /api/settings/<get>, POST /api/setsettings/<post>, estilo de cuerpo)
#  "form": cuerpo `json=<JSON codificado>` (el servidor salta los 5 primeros caracteres)
#  "raw":  JSON codificado sin prefijo (solo alarmas)
SETTINGS_SECTIONS: dict[str, tuple[str, str, str]] = {
    "program": ("programdata.json", "updateprogramconfig.json", "form"),
    "station": ("stationdata.json", "updatestationconfig.json", "form"),
    "internet": ("internetdata.json", "updateinternetconfig.json", "form"),
    "thirdparty": ("thirdpartydata.json", "updatethirdpartyconfig.json", "form"),
    "extrasensors": ("extrasensordata.json", "updateextrasensorconfig.json", "form"),
    "extrawebfiles": ("extrawebfiles.json", "updateextrawebfiles.json", "form"),
    "calibration": ("calibrationdata.json", "updatecalibrationconfig.json", "form"),
    "language": ("langdata.json", "updatelanguage.json", "form"),
    "noaa": ("noaadata.json", "updatenoaaconfig.json", "form"),
    "mysql": ("mysqldata.json", "updatemysqlconfig.json", "form"),
    "alarms": ("alarms.json", "updatealarmconfig.json", "raw"),
    "useralarms": ("useralarms.json", "updateuseralarms.json", "form"),
    "wizard": ("wizard.json", "wizard.json", "form"),
    "customlogs_interval": ("customlogsintvl.json", "updatecustomlogsintvl.json", "form"),
    "customlogs_daily": ("customlogsdaily.json", "updatecustomlogsdaily.json", "form"),
    "display": ("displayoptions.json", "updatedisplay.json", "form"),
    "httpfiles": ("httpfiles.json", "updatehttpfiles.json", "form"),
    "mqtt": ("mqttdata.json", "updatemqttconfig.json", "form"),
}


SettingsSection = Literal[
    "program", "station", "internet", "thirdparty", "extrasensors", "extrawebfiles", "calibration", "language",
    "noaa", "mysql", "alarms", "useralarms", "wizard", "customlogs_interval", "customlogs_daily", "display",
    "httpfiles", "mqtt",
]




def _maybe_mask(text: str) -> str:
    if not (config.CUMULUS_MASK_SECRETS or safety.current_scope() == "readonly"):
        return text
    try:
        return json.dumps(utils._mask_secrets_in(json.loads(text)), ensure_ascii=False)
    except ValueError:
        return text


def _diff_paths(old: Any, new: Any, prefix: str = "") -> dict[str, dict]:
    """Campos que cambian entre dos estructuras JSON: {ruta: {antes, despues}} (secretos siempre enmascarados)."""
    out: dict[str, dict] = {}
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new), key=str):
            path = f"{prefix}.{k}" if prefix else str(k)
            if k not in old or k not in new:
                out[path] = {"antes": old.get(k), "despues": new.get(k)}
            else:
                out.update(_diff_paths(old[k], new[k], path))
    elif old != new:
        out[prefix] = {"antes": old, "despues": new}
    if prefix == "":
        return _mask_secrets_in_diff(out)
    return out


def _mask_secrets_in_diff(diff: dict[str, dict]) -> dict[str, dict]:
    masked = {}
    for path, change in diff.items():
        if utils.SECRET_KEY_RE.search(path.rsplit(".", 1)[-1]):
            change = {k: ("***" if isinstance(v, str) and v else v) for k, v in change.items()}
        masked[path] = change
    return masked


def _encode_settings_body(style: str, payload: dict) -> str:
    encoded = urllib.parse.quote(json.dumps(payload, ensure_ascii=False))
    return "json=" + encoded if style == "form" else encoded


async def _settings_update(section: str, settings: dict[str, Any] | str, dry_run: bool = False,
                           replace: bool = False) -> str:
    """Lee la sección actual, fusiona los campos indicados (o la sustituye con replace=True) y la envía con el formato
    de cuerpo que espera CumulusMX. Si no puede leer la configuración actual NO escribe a ciegas."""
    if (err := safety._check_write_enabled()):
        return err
    if section not in SETTINGS_SECTIONS:
        raise ValueError(f"section debe ser una de {sorted(SETTINGS_SECTIONS)}")
    partial = utils._normalize_settings_payload(settings)
    get_path, post_path, style = SETTINGS_SECTIONS[section]
    current = await client._json(f"/api/settings/{get_path}")
    if not isinstance(current, dict):
        raise ValueError(f"CumulusMX devolvió una configuración inesperada para '{section}'; no se modifica nada")
    if replace:
        merged = partial
    elif section == "alarms" and not any(k in partial for k in ("data", "units", "email")):
        section_data = current.get("data") if isinstance(current.get("data"), dict) else {}
        merged = {**current, "data": utils._deep_merge(section_data, partial)}
    else:
        merged = utils._deep_merge(current, partial)
    changes = _diff_paths(current, merged)
    result: dict[str, Any] = {"seccion": section, "campos_modificados": changes, "modo": "sustitucion" if replace else "fusion"}
    if dry_run:
        result["aplicado"] = False
        result["nota"] = "Vista previa: no se ha enviado nada a CumulusMX."
        return json.dumps(result, ensure_ascii=False)
    if not changes:
        result["aplicado"] = False
        result["nota"] = "Sin cambios: la configuración ya tiene esos valores."
        return json.dumps(result, ensure_ascii=False)
    backup_id = backups.save("settings", section, current, "update_settings")
    result["copia_id"] = backup_id
    if backups.enabled() and backup_id is None:
        result["aviso"] = "No se pudo guardar la copia previa de seguridad (ver el log del servidor)."
    headers = JSON_HEADERS if style == "raw" else FORM_HEADERS
    response = await client._post(f"/api/setsettings/{post_path}", content=_encode_settings_body(style, merged), headers=headers)
    result["aplicado"] = True
    result["respuesta"] = response
    ignored = await _ignored_changes(get_path, merged, changes)
    if ignored:
        result["no_guardados"] = ignored
        result["aviso"] = ("CumulusMX respondió bien pero NO guardó estos campos: " + ", ".join(ignored) + ". Suele pasar con un "
                           "servicio deshabilitado (CumulusMX solo guarda los campos de un servicio si está habilitado: pon "
                           "enabled=true en la misma llamada).")
        if len(ignored) == len(changes):
            result["aplicado"] = False
            result["nota"] = result["aviso"]
    return json.dumps(result, ensure_ascii=False)


async def _restore_section(section: str, content: dict[str, Any]) -> str:
    """Restaura una sección completa desde su copia.

    CumulusMX no guarda los campos de un servicio deshabilitado: si la copia tiene un servicio apagado cuyos campos cambiaron
    después (p. ej. se activó FTP con otro servidor), al restaurar se apaga pero conserva los campos nuevos. En ese caso se
    repite en dos pasos: se envía la copia con esos servicios habilitados (así guarda sus campos) y enseguida la copia tal cual."""
    first = json.loads(await _settings_update(section, content, replace=True))
    unsaved = first.get("no_guardados") or {}
    if not unsaved or not isinstance(content, dict):
        return json.dumps(first, ensure_ascii=False)
    staged = copy.deepcopy(content)
    toggled: list[str] = []
    for path in unsaved:
        node: Any = staged
        parts = path.split(".")
        for i, key in enumerate(parts[:-1]):
            node = node.get(key) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                break
            if node.get("enabled") is False:
                node["enabled"] = True
                toggled.append(".".join(parts[: i + 1]))
    if not toggled:
        return json.dumps(first, ensure_ascii=False)
    await _settings_update(section, staged, replace=True)
    final = json.loads(await _settings_update(section, content, replace=True))
    final["pasos"] = (f"Restaurado en dos pasos: {sorted(set(toggled))} se habilitaron un instante para que CumulusMX guardara "
                      "sus campos y se volvieron a deshabilitar, como en la copia.")
    return json.dumps(final, ensure_ascii=False)


def _same_setting(wanted: Any, actual: Any) -> bool:
    """Igualdad tolerante: CumulusMX devuelve vacío como null o "" y los números a veces como texto, y recorta los espacios
    de los extremos de sus textos al guardarlos (" x" se guarda como "x")."""
    if isinstance(wanted, str):
        wanted = wanted.strip()
    if isinstance(actual, str):
        actual = actual.strip()
    if wanted in (None, "") and actual in (None, ""):
        return True
    try:
        return float(wanted) == float(actual)
    except (TypeError, ValueError):
        return wanted == actual


async def _ignored_changes(get_path: str, merged: dict[str, Any], changes: dict[str, dict]) -> dict[str, dict]:
    """Campos pedidos que, al volver a leer la sección, CumulusMX no tiene guardados: {ruta: {pedido, actual}}.

    Los secretos no se comparan (el diff los enmascara) ni se devuelven. Si no se puede releer, no se afirma nada."""
    try:
        fresh = await client._json(f"/api/settings/{get_path}")
    except Exception:  # pragma: no cover - relectura opcional
        return {}
    if not isinstance(fresh, dict):
        return {}
    out: dict[str, dict] = {}
    for path in changes:
        if utils.SECRET_KEY_RE.search(path.rsplit(".", 1)[-1]):
            continue
        wanted, actual = _lookup(merged, path), _lookup(fresh, path)
        if not _same_setting(wanted, actual):
            out[path] = {"pedido": wanted, "actual": actual}
    return out


def _lookup(node: Any, path: str) -> Any:
    for key in path.split("."):
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return None
    return node


async def legacy_update(section: str, settings: dict[str, Any] | str) -> str:
    """Para los actualizadores clásicos (estación, calibración, alarmas): mismo motor seguro, pero devuelve el texto de
    respuesta de CumulusMX tal cual, como siempre. Si no puede leer la configuración actual lanza ValueError (no escribe)."""
    out = json.loads(await _settings_update(section, settings))
    return out["respuesta"] if out.get("aplicado") else out.get("nota", "")
