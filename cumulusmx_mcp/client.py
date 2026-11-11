"""Cliente HTTP hacia la API de CumulusMX: autenticación básica, reintentos, recorte de respuestas."""
import asyncio
import os
from typing import Any

import httpx

from . import config
from .config import logger


_client: httpx.AsyncClient | None = None


def _get_auth() -> tuple[str, str] | None:
    user = os.environ.get("CUMULUS_USER")
    pwd = os.environ.get("CUMULUS_PASSWORD")
    if user:
        return (user, pwd or "")
    return None


def _cli() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30)
    return _client


async def _api_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    content: bytes | str | None = None,
    data: Any = None,
    json_data: Any = None,
    headers: dict | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    cli = _cli()
    auth = _get_auth()
    req_headers = dict(headers or {})
    if isinstance(content, str):
        content = content.encode("utf-8")
    return await cli.request(
        method=method,
        url=f"{config.BASE}{path}",
        params=params,
        content=content,
        data=data,
        json=json_data,
        headers=req_headers,
        auth=auth,
        timeout=timeout,
    )


async def _request_get(path: str, params: dict | None = None) -> httpx.Response:
    """GET con reintentos (máx. CUMULUS_RETRIES) ante errores de conexión, timeouts y 502/504. Los POST no se
    reintentan nunca (no son idempotentes). Backoff exponencial corto: 0,3 s, 0,6 s..."""
    for attempt in range(config.CUMULUS_RETRIES + 1):
        last = attempt == config.CUMULUS_RETRIES
        try:
            r = await _api_request("GET", path, params=params, timeout=config.CUMULUS_TIMEOUT)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            if last:
                raise
            logger.warning("GET %s falló (%s); reintento %d/%d", path, type(e).__name__, attempt + 1, config.CUMULUS_RETRIES)
        else:
            if r.status_code not in config.RETRY_STATUS or last:
                return r
            logger.warning("GET %s devolvió %s; reintento %d/%d", path, r.status_code, attempt + 1, config.CUMULUS_RETRIES)
        await asyncio.sleep(config.RETRY_BACKOFF_S * 2 ** attempt)
    raise RuntimeError("inalcanzable")  # pragma: no cover


def _clip(t: str) -> str:
    if len(t) > config.MAX_CHARS:
        return t[:config.MAX_CHARS] + f"\n…[truncado: {len(t)} caracteres; usa rangos más pequeños o downsample]"
    return t


async def _get(path: str, params: dict | None = None) -> str:
    try:
        r = await _request_get(path, params)
        r.raise_for_status()
        return _clip(r.text)
    except httpx.HTTPStatusError as e:
        return f"ERROR HTTP {e.response.status_code} en {path}: {e.response.text[:300]}"
    except httpx.HTTPError as e:
        return f"ERROR de conexión con CumulusMX ({type(e).__name__}): {e}"


async def _json(path: str, params: dict | None = None):
    try:
        r = await _request_get(path, params)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        raise ValueError(f"HTTP {e.response.status_code} en {path}: {e.response.text[:200]}")
    except httpx.HTTPError as e:
        raise ValueError(f"error de conexión con CumulusMX: {e}")


async def _post(
    path: str,
    *,
    params: dict | None = None,
    content: bytes | str | None = None,
    data: Any = None,
    json_data: Any = None,
    headers: dict | None = None,
    timeout: float = 60.0,
) -> str:
    try:
        r = await _api_request(
            "POST",
            path,
            params=params,
            content=content,
            data=data,
            json_data=json_data,
            headers=headers,
            timeout=timeout,
        )
        r.raise_for_status()
        return _clip(r.text)
    except httpx.HTTPStatusError as e:
        return f"ERROR HTTP {e.response.status_code} en {path}: {e.response.text[:300]}"
    except httpx.HTTPError as e:
        return f"ERROR de conexión con CumulusMX ({type(e).__name__}): {e}"


def _tags_path(tags: list[str], plain_numbers: bool = True) -> str:
    """Ruta de /api/tags/process.json. CumulusMX acepta el parámetro especial `rc` (debe ir el primero) que aplica el
    formato 'remove commas' a todos los tags: sin él, una estación con configuración regional española devuelve
    '10,0' (coma decimal), ambiguo y no convertible a número."""
    return "/api/tags/process.json?" + ("rc&" if plain_numbers else "") + "&".join(tags)
