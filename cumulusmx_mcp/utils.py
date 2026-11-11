"""Utilidades puras sin dependencias del servidor: fechas, números, fusión de dicts, limpieza de HTML."""
import datetime
import html
import json
import math
import re
from typing import Any





DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$")


TAG_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _normalize_settings_payload(settings: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(settings, str):
        try:
            parsed = json.loads(settings)
        except Exception as e:
            raise ValueError(f"JSON de configuración inválido: {e}")
        if not isinstance(parsed, dict):
            raise ValueError("El JSON de configuración debe ser un objeto/diccionario")
        return parsed
    if not isinstance(settings, dict):
        raise ValueError("Los ajustes deben proporcionarse como diccionario o cadena JSON")
    return settings


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    out = dict(target)
    for k, v in source.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _clean(t: str) -> str:
    t = re.sub(r"<br\s*/?>", " ", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).replace("\xa0", " ").strip()


def _check_date(d: str, name: str = "fecha") -> datetime.date:
    try:
        return datetime.datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{name} inválida, usa yyyy-mm-dd")


def _num(x: Any) -> float | None:
    if x is None or x in ("", "-", "None", "null"):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    # CumulusMX usa -9999 / 9999 (DefaultHiVal / DefaultLoVal) como "sin dato"; ninguna columna del dayfile
    # puede valer legítimamente <= -999.
    if math.isnan(f) or f <= -999 or f >= 9999:
        return None
    return f


def _today() -> datetime.date:
    return datetime.date.today()
