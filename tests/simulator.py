"""Simulador de la API de CumulusMX 5.2.0 para pruebas de extremo a extremo.

Replica cómo el servidor real LEE los cuerpos (según Api.cs, DataEditor.cs y Settings/*.cs): saltarse los 5 primeros
caracteres (`json=`), `WebUtility.UrlDecode` (convierte '+' en espacio), parseo de números con la cultura regional,
comprobación de fechas al editar el dayfile... Así los tests detectan errores de formato que un mock no vería.
Mantiene estado en memoria (ajustes, dayfile, récords) y registra las peticiones recibidas.
"""
import json
import urllib.parse
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route


def csharp_url_decode(text: str) -> str:
    """WebUtility.UrlDecode: %XX y '+' -> espacio."""
    return urllib.parse.unquote_plus(text)


class FakeCumulus:
    def __init__(self, decimal_comma: bool = True):
        self.decimal_comma = decimal_comma
        self.requests: list[tuple[str, str, str]] = []   # (método, ruta, cuerpo)
        self.settings: dict[str, Any] = {
            "mysqldata.json": {"accessible": False,
                               "server": {"host": "db", "port": 3306, "user": "u", "pass": "s3cret", "database": "w",
                                          "advanced": {"sslMode": 1, "tlsVers": "TLS 1.2"}},
                               "monthly": {"enabled": False, "table": "Monthly"}},
            "mqttdata.json": {"server": "", "port": 1883, "password": ""},
            "stationdata.json": {"AnnualRainfall": {"ytdamount": 50.0, "rainseasonstart": 1}},
            "alarms.json": {"data": {"BatteryLow": {"enabled": False}}, "units": {"temp": "C"},
                            "email": {"fromEmail": "a@b.c", "password": "x"}},
        }
        self.dayfile = [
            [1, "24/08/19", "15.0", "142", "15:04", "12.1"],
            [2, "25/08/19", "19.0", "138", "14:20", "15.4"],
        ]
        self.records = {"alltime": {"highTemp": (34.6, "25 July 2019 15:39")}}
        self.temp = "10,0" if decimal_comma else "10.0"
        self.created_tables: list[str] = []
        self.sql_posts: list[str] = []

    # ---- helpers
    def _json(self, obj: Any, status: int = 200) -> Response:
        return Response(json.dumps(obj), status_code=status, media_type="application/json")

    async def _body(self, request: Request) -> str:
        return (await request.body()).decode("utf-8")

    # ---- /api/settings/<x> y /api/setsettings/<x>
    async def get_settings(self, request: Request) -> Response:
        name = request.path_params["req"]
        if name not in self.settings:
            return Response("not found", status_code=404)
        return self._json(self.settings[name])

    async def set_settings(self, request: Request) -> Response:
        name = request.path_params["req"]
        body = await self._body(request)
        self.requests.append(("POST", f"/api/setsettings/{name}", body))
        mapping = {"updatemysqlconfig.json": "mysqldata.json", "updatemqttconfig.json": "mqttdata.json",
                   "updatestationconfig.json": "stationdata.json", "updatealarmconfig.json": "alarms.json"}
        if name.startswith(("createmonthly", "createdayfile", "createrealtime", "updatemonthly", "updatedayfile", "updaterealtime")):
            self.created_tables.append(name)
            return self._json({"result": "ok"})
        if name == "updatealarmconfig.json":      # AlarmSettings: UrlDecode(data) sin prefijo
            try:
                parsed = json.loads(csharp_url_decode(body))
            except ValueError:
                return Response("Error de-serializing", status_code=500)
        elif name in mapping:                      # resto: data[5..] + UrlDecode
            try:
                parsed = json.loads(csharp_url_decode(body[5:]))
            except ValueError:
                return Response("Error de-serializing", status_code=500)
        else:
            return Response("not found", status_code=404)
        self.settings[mapping[name]] = parsed
        return PlainTextResponse("success")

    # ---- datos
    async def tags(self, request: Request) -> Response:
        query = request.url.query
        parts = query.split("&") if query else []
        rc = bool(parts) and parts[0] == "rc"
        if rc:
            parts = parts[1:]
        out = {}
        for tag in parts:
            if tag == "temp":
                out[tag] = self.temp.replace(",", ".") if rc else self.temp
            else:
                out[tag] = ""
        return self._json(out)

    async def dayfile_get(self, request: Request) -> Response:
        q = request.query_params
        start, length = int(q.get("start", 0)), int(q.get("length", 10))
        rows = self.dayfile[start:start + length]
        return self._json({"draw": 1, "recordsTotal": len(self.dayfile), "recordsFiltered": len(self.dayfile), "data": rows})

    async def edit_dayfile(self, request: Request) -> Response:
        body = await self._body(request)
        self.requests.append(("POST", "/api/edit/dayfile", body))
        new = json.loads(body)
        line = new["lines"][0] - 1
        if new["action"] == "Edit":
            if self.dayfile[line][1] != new["data"][0][0]:      # comprueba que las fechas coinciden
                return self._json({"errors": {"Logfile": ["fecha distinta"]}}, 500)
            self.dayfile[line] = [line + 1, *new["data"][0]]
        else:
            del self.dayfile[line]
        return self._json({"errors": None})

    async def edit_alltime(self, request: Request) -> Response:
        body = await self._body(request)
        self.requests.append(("POST", "/api/edit/alltime", body))
        text = urllib.parse.unquote(body)                       # Uri.UnescapeDataString: no convierte '+'
        parts = text.split("&")
        field, txt_value, txt_time = (p.split("=")[1] for p in parts[:3])
        # double.Parse con cultura regional: con coma decimal "34.6" se leería como 346 (el punto es separador de miles)
        value = float(txt_value.replace(".", "").replace(",", ".")) if self.decimal_comma else float(txt_value)
        time = txt_time.replace("+", " ")
        self.records.setdefault("alltime", {})[field] = (value, time)
        return PlainTextResponse("Data updated")

    async def edit_records_get(self, request: Request) -> Response:
        return self._json({"data": self.records.get("alltime", {})})

    async def version(self, request: Request) -> Response:
        return self._json({"Version": "5.2.0", "Build": "5026"})

    def build(self) -> Starlette:
        r = [
            Route("/api/settings/{req}", self.get_settings),
            Route("/api/setsettings/{req}", self.set_settings, methods=["POST"]),
            Route("/api/tags/process.json", self.tags),
            Route("/api/data/dayfile", self.dayfile_get),
            Route("/api/edit/dayfile", self.edit_dayfile, methods=["POST"]),
            Route("/api/edit/alltime", self.edit_alltime, methods=["POST"]),
            Route("/api/edit/alltimerecords.json", self.edit_records_get),
            Route("/api/info/version.json", self.version),
        ]
        return Starlette(routes=r)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.build()), base_url="http://cumulus")
