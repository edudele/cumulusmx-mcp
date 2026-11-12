"""Cliente HTTP hacia la API de CumulusMX: autenticación básica, reintentos, recorte de respuestas."""
import asyncio
import json
import os
import time
from typing import Any

import httpx

from . import config, metrics
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
    started = time.monotonic()
    try:
        response = await cli.request(
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
    except httpx.HTTPError:
        _record_request(method, "conexion", started, up=False)
        raise
    ok = response.status_code < 400
    _record_request(method, "ok" if ok else "http_error", started, up=response.status_code < 500)
    return response


def _record_request(method: str, outcome: str, started: float, up: bool) -> None:
    """Métricas de cada petición a CumulusMX (un error 4xx sigue contando como estación alcanzable)."""
    metrics.metric_inc("cumulusmx_mcp_cumulus_requests_total", method=method, resultado=outcome)
    metrics.metric_observe("cumulusmx_mcp_cumulus_request_duration_seconds", time.monotonic() - started, method=method)
    metrics.metric_gauge("cumulusmx_mcp_cumulus_up", 1 if up else 0)
    if up:
        metrics.metric_gauge("cumulusmx_mcp_cumulus_last_success_timestamp_seconds", time.time())


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
    """Recorta las respuestas demasiado grandes. Si son JSON, devuelve un JSON válido (con "truncado": true) en vez de un
    texto cortado a mitad que ningún cliente podría interpretar; si no, añade un aviso al final."""
    limit = config.MAX_CHARS
    if len(t) <= limit:
        return t
    hint = "usa rangos más pequeños, filtros o downsample"
    if t.lstrip()[:1] in "[{":
        try:
            data = json.loads(t)
        except ValueError:
            data = None
        if data is not None:
            return _clip_json(data, len(t), limit, hint)
    return t[:limit] + f"\n…[truncado: {len(t)} caracteres; {hint}]"


def _clip_json(data, total: int, limit: int, hint: str) -> str:
    """Reduce la lista de filas más larga (la propia respuesta o su clave "data") hasta que quepa; si no hay filas, recorta."""
    holder, key = (data, "data") if isinstance(data, dict) and isinstance(data.get("data"), list) else (None, None)
    rows = data["data"] if holder is not None else data if isinstance(data, list) else None

    def envelope(**extra) -> str:
        return json.dumps({"truncado": True, "caracteres_totales": total, "aviso": hint, **extra}, ensure_ascii=False)

    if rows:
        keep = len(rows)
        while keep > 1:
            keep = max(1, keep // 2 if keep > 8 else keep - 1)
            kept = rows[:keep]
            body = {**data, key: kept} if holder is not None else kept
            out = envelope(filas_totales=len(rows), filas_devueltas=keep, contenido=body)
            if len(out) <= limit:
                return out
    # sin filas que recortar (o ni una cabe): texto parcial dentro de un envoltorio válido
    return envelope(contenido_parcial=json.dumps(data, ensure_ascii=False)[:max(0, limit - 400)])


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
        if not r.content.strip():
            raise ValueError(f"CumulusMX devolvió una respuesta vacía en {path}. En una instalación recién estrenada (el día de "
                             "inicio de registros es hoy) CumulusMX aún no devuelve el histórico; si no es el caso, revisa su log.")
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
