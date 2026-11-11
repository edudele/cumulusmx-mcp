"""Motor genérico de ajustes: lectura-fusión-escritura con vista previa, enmascarado de secretos y formato de cuerpo."""
import json
import re
import urllib.parse
from typing import Any, Literal


from . import client
from . import config
from . import safety
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


SECRET_KEY_RE = re.compile(r"(pass(word)?|pwd|secret|token|api[-_]?key|psk)$", re.IGNORECASE)


def _mask_secrets_in(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if isinstance(v, str) and v and SECRET_KEY_RE.search(str(k)) else _mask_secrets_in(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_secrets_in(v) for v in obj]
    return obj


def _maybe_mask(text: str) -> str:
    if not config.CUMULUS_MASK_SECRETS:
        return text
    try:
        return json.dumps(_mask_secrets_in(json.loads(text)), ensure_ascii=False)
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
        if SECRET_KEY_RE.search(path.rsplit(".", 1)[-1]):
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
    headers = JSON_HEADERS if style == "raw" else FORM_HEADERS
    response = await client._post(f"/api/setsettings/{post_path}", content=_encode_settings_body(style, merged), headers=headers)
    result["aplicado"] = True
    result["respuesta"] = response
    return json.dumps(result, ensure_ascii=False)
