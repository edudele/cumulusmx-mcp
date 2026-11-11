"""Copias previas a cada escritura y su restauración.

Antes de cambiar una sección de ajustes, una línea del dayfile o los récords se guarda el estado anterior en
CUMULUS_BACKUP_DIR (un JSON por copia; por defecto el directorio temporal: monta un volumen para conservarlas).
Las copias pueden contener contraseñas: se crean con permisos 0600 en un directorio 0700.
"""
import datetime
import json
import os
import re
from typing import Any

from . import config
from .config import logger

_ID_RE = re.compile(r"^[0-9A-Za-z._-]{1,120}$")


def enabled() -> bool:
    return bool(config.BACKUP_DIR)


def _dir() -> str:
    os.makedirs(config.BACKUP_DIR, mode=0o700, exist_ok=True)
    return config.BACKUP_DIR


def save(kind: str, target: str, payload: Any, tool: str) -> str | None:
    """Guarda una copia y devuelve su id. Devuelve None (y lo registra) si está desactivado o falla: no bloquea la operación."""
    if not enabled():
        return None
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        safe = re.sub(r"[^0-9A-Za-z_-]+", "_", f"{kind}-{target}")[:60]
        backup_id = f"{now:%Y%m%dT%H%M%S%f}Z-{safe}"
        record = {"id": backup_id, "creada": now.isoformat(timespec="seconds"), "tipo": kind, "objetivo": target,
                  "herramienta": tool, "contenido": payload}
        fd = os.open(os.path.join(_dir(), backup_id + ".json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
        _prune()
        return backup_id
    except Exception as exc:
        logger.error("No se pudo guardar la copia previa (%s %s): %s", kind, target, exc)
        return None


def _files() -> list[str]:
    if not enabled() or not os.path.isdir(config.BACKUP_DIR):
        return []
    return sorted(f for f in os.listdir(config.BACKUP_DIR) if f.endswith(".json"))


def _prune() -> None:
    files = _files()
    for old in files[: max(0, len(files) - config.BACKUP_KEEP)]:
        try:
            os.remove(os.path.join(config.BACKUP_DIR, old))
        except OSError:  # pragma: no cover
            pass


def load(backup_id: str) -> dict[str, Any]:
    """Copia completa. ValueError si el id no es válido o no existe (el id nunca se usa como ruta sin validar)."""
    if not _ID_RE.match(backup_id or ""):
        raise ValueError("backup_id inválido")
    path = os.path.join(config.BACKUP_DIR or "", backup_id + ".json")
    if not enabled() or not os.path.isfile(path):
        raise ValueError(f"No existe la copia '{backup_id}'")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def recent(kind: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Metadatos de las copias más recientes (sin contenido)."""
    out = []
    for name in reversed(_files()):
        try:
            with open(os.path.join(config.BACKUP_DIR, name), encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if kind and rec.get("tipo") != kind:
            continue
        out.append({k: rec.get(k) for k in ("id", "creada", "tipo", "objetivo", "herramienta")})
        if len(out) >= max(1, min(limit, 200)):
            break
    return out
