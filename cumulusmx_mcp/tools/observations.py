"""Tools de observación actual: datos en vivo, tags, hoy/ayer, sensores extra, WebSocket."""
import asyncio
import base64
import json
import re
from typing import Any, Literal


from .. import __version__, client
from .. import config
from .. import utils
from ..app import READ_ONLY, mcp


@mcp.tool(annotations=READ_ONLY)
async def ping() -> str:
    """Comprueba que el MCP responde."""
    return "pong"


@mcp.tool(annotations=READ_ONLY)
async def current_data() -> str:
    """Condiciones actuales completas de la estación (JSON): temperatura, humedad, viento, lluvia, presión, etc."""
    return await client._get("/api/data/currentdata")


@mcp.tool(annotations=READ_ONLY)
async def get_tags(tags: list[str], plain_numbers: bool = True) -> str:
    """Valores de webtags de CumulusMX (p.ej. ['temp','hum','press','wlatest']). Devuelve JSON {tag: valor}.
    Todos los valores llegan como texto. plain_numbers=True (por defecto) aplica el formato 'rc' de CumulusMX:
    decimales con punto ('10.0' en vez de '10,0'). Con False se respeta el formato regional de la estación."""
    if not tags or len(tags) > 50 or not all(utils.TAG_RE.match(t) for t in tags):
        raise ValueError("tags inválidos (solo letras, números y _; máximo 50)")
    return await client._get(client._tags_path(tags, plain_numbers))


@mcp.tool(annotations=READ_ONLY)
async def today_yesterday(kind: Literal["temp", "hum", "rain", "wind", "pressure", "solar"]) -> str:
    """Valores de hoy y ayer para una magnitud."""
    return await client._get(f"/api/todayyest/{kind}.json")


@mcp.tool(annotations=READ_ONLY)
async def station_info(kind: Literal["version", "units", "alarms", "snowinfo", "timeformat", "wsport", "displaylangs",
                                     "dateformat", "csvseparator"]) -> str:
    """Información de la estación: versión, unidades, alarmas, formato de fecha (dateformat), separador CSV
    (csvseparator), idiomas, puerto WebSocket..."""
    ext = "txt" if kind in ("dateformat", "csvseparator") else "json"
    return await client._get(f"/api/info/{kind}.{ext}")


@mcp.tool(annotations=READ_ONLY)
async def today_all(strip_html: bool = True) -> str:
    """Hoy vs ayer de TODAS las magnitudes (temp, hum, rain, wind, pressure, solar) en una sola llamada."""
    keys = ("temp", "hum", "rain", "wind", "pressure", "solar")
    texts = await asyncio.gather(*(client._get(f"/api/todayyest/{k}.json") for k in keys))
    res = {k: (json.loads(t) if t.startswith("{") else t) for k, t in zip(keys, texts)}
    s = json.dumps(res, ensure_ascii=False)
    return utils._clean(s) if strip_html else s


@mcp.tool(annotations=READ_ONLY)
async def snapshot() -> str:
    """Foto rápida de la estación: temperatura, sensación, humedad, rocío, presión y tendencia, viento/racha/dirección,
    lluvia (hoy/mes/año/tasa), sol/UV, extremos de hoy, amanecer/atardecer, luna y estado de la estación.
    Los números llegan con punto decimal (formato 'rc' de CumulusMX); las unidades van en los tags *unit."""
    tags = ["temp", "tempunit", "feelslike", "dew", "hum", "press", "presstrend", "pressunit", "wlatest", "wgust",
            "bearing", "currentwdir", "windunit", "rrate", "rfall", "rmonth", "ryear", "rainunit", "SolarRad", "UV",
            "tempTH", "TtempTH", "tempTL", "TtempTL", "wspeedTH", "pressTH", "pressTL", "humTH", "humTL",
            "sunrise", "sunset", "daylength", "moonphase", "DataStopped", "version", "build", "stationtype", "time", "date"]
    return await client._get(client._tags_path(tags))


@mcp.tool(annotations=READ_ONLY)
async def render_template(template: str) -> str:
    """Evalúa una plantilla de webtags con modificadores, p.ej.
    '<#temp dp=2> <#tempH> <#time format="HH:mm"> <#RecentOutsideTemp d=1>'. Solo se admiten etiquetas <#nombre ...> y texto simple."""
    if len(template) > 1000:
        raise ValueError("plantilla demasiado larga (máx 1000)")
    if not re.fullmatch(r"[A-Za-z0-9_ .,:;%/\-=\"'<>#()\[\]]*", template) or "<#" not in template:
        raise ValueError("plantilla con caracteres no permitidos o sin etiquetas <#...>")
    return await client._post("/api/tags/process.txt", content=template.encode(), headers={"Content-Type": "text/plain"})


@mcp.tool(annotations=READ_ONLY)
async def logfile(start: str, end: str, offset: int = 0, length: int = 100, extra: bool = False) -> str:
    """Log de intervalo (datos cada pocos minutos) entre dos fechas yyyy-mm-dd, paginado (length máx 300).
    extra=True para el log de sensores extra."""
    s, e = utils._check_date(start, "start"), utils._check_date(end, "end")
    if e < s or (e - s).days > 31:
        raise ValueError("rango inválido (máx 31 días)")
    p = {"from": start, "to": end, "draw": 1, "start": max(0, offset), "length": max(1, min(length, 300)),
         "search[value]": ""}
    return await client._get("/api/data/extralogfile" if extra else "/api/data/logfile", p)


@mcp.tool(annotations=READ_ONLY)
async def recent_data(offset: int = 0, length: int = 100) -> str:
    """Tabla de datos recientes de la estación (últimas horas), paginada (length máx 300)."""
    return await client._get("/api/data/recentdata", {"draw": 1, "start": max(0, offset),
                                               "length": max(1, min(length, 300)), "search[value]": ""})


EXTRA_SENSORS: tuple[str, ...] = (
    "temp", "hum", "dew", "soiltemp", "soilmoisture", "soilec", "leaf8", "airqual", "lightning", "usertemp",
    "laserdepth", "laserdistance", "snow24h", "snowseason", "airLinkCountsOut", "airLinkAqiOut", "airLinkPctOut",
    "airLinkCountsIn", "airLinkAqiIn", "airLinkPctIn", "co2sensor", "bgt",
)


@mcp.tool(annotations=READ_ONLY)
async def extra_sensors(sensor: Literal[
    "temp", "hum", "dew", "soiltemp", "soilmoisture", "soilec", "leaf8", "airqual", "lightning", "usertemp",
    "laserdepth", "laserdistance", "snow24h", "snowseason", "airLinkCountsOut", "airLinkAqiOut", "airLinkPctOut",
    "airLinkCountsIn", "airLinkAqiIn", "airLinkPctIn", "co2sensor", "bgt",
]) -> str:
    """Valores actuales de los sensores extra de CumulusMX: temp/hum/dew (extra), soiltemp, soilmoisture, soilec,
    leaf8 (humectación foliar), airqual, lightning, usertemp, laserdepth/laserdistance, snow24h/snowseason,
    airLink* (calidad del aire Davis AirLink, exterior 'Out' / interior 'In'), co2sensor y bgt (globo negro).
    Solo devuelve datos de los sensores que estén configurados en la estación."""
    if sensor not in EXTRA_SENSORS:
        raise ValueError(f"sensor debe ser uno de {list(EXTRA_SENSORS)}")
    return await client._get(f"/api/extra/{sensor}.json")


ALLOWED_GET = re.compile(
    r"^/api/(data/(currentdata|dayfile|logfile|extralogfile|recentdata|diarydata|diarysummary)"
    r"|tags/process\.json|graphdata/[a-z0-9]+\.json|dailygraphdata/[a-z0-9]+\.json"
    r"|records/[a-z0-9/]+\.json|todayyest/[a-z]+\.json|extra/[A-Za-z0-9_.]+|info/[a-z]+\.json"
    r"|reports/[A-Za-z0-9]+)$")


@mcp.tool(annotations=READ_ONLY)
async def api_get(path: str, query: dict[str, str] | None = None) -> str:
    """Acceso genérico de SOLO LECTURA a endpoints GET de CumulusMX por lista blanca (p.ej. path='/api/extra/xyz').
    Prohibido: settings, utils, genreports, uploadreport, mysqlcache, errorlog, station/*."""
    if ".." in path or not ALLOWED_GET.fullmatch(path):
        raise ValueError("ruta no permitida (solo lectura, lista blanca)")
    q = {str(k)[:50]: str(v)[:200] for k, v in (query or {}).items()}
    return await client._get(path, q or None)


@mcp.tool(annotations=READ_ONLY)
async def server_status() -> str:
    """Diagnóstico: comprueba que CumulusMX responde y devuelve su versión."""
    ver = await client._get("/api/info/version.json")
    return json.dumps({"mcp": "cumulusmx-mcp", "mcp_version": __version__, "cumulusmx_version": ver},
                      ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def get_current_conditions_text() -> str:
    """Texto de 'condiciones actuales' que CumulusMX publica (el que cambia edit_current_conditions)."""
    return await client._get("/api/edit/currentcond.json")


@mcp.tool(annotations=READ_ONLY)
async def live_sample(seconds: float = 10.0, max_messages: int = 3) -> str:
    """Escucha el WebSocket de tiempo real de CumulusMX (/ws/) y devuelve los primeros mensajes (el primero llega al
    conectar; los siguientes en cada actualización de la estación, normalmente cada pocos segundos). Cada mensaje es el
    mismo JSON que current_data. seconds máx 60, max_messages máx 20."""
    seconds = max(1.0, min(float(seconds), 60.0))
    max_messages = max(1, min(int(max_messages), 20))
    try:
        import websockets
    except ImportError:  # pragma: no cover
        raise ValueError("Falta la librería 'websockets' (pip install websockets)")
    url = config.BASE.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws/"
    headers = {}
    if (auth := client._get_auth()):
        headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    messages: list[Any] = []

    async def collect() -> None:
        async with websockets.connect(url, additional_headers=headers or None, open_timeout=10) as ws:
            while len(messages) < max_messages:
                raw = await ws.recv()
                try:
                    messages.append(json.loads(raw))
                except (TypeError, ValueError):
                    messages.append(raw)

    try:
        await asyncio.wait_for(collect(), timeout=seconds)
        timed_out = False
    except asyncio.TimeoutError:
        timed_out = True
    except Exception as e:  # conexión rechazada, 403, etc.
        if not messages:
            return json.dumps({"error": f"No se pudo leer el WebSocket {url}: {type(e).__name__}: {e}"}, ensure_ascii=False)
        timed_out = False
    return json.dumps({"url": url, "n_mensajes": len(messages), "tiempo_agotado": timed_out, "mensajes": messages},
                      ensure_ascii=False)
