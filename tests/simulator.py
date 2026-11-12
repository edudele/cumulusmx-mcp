"""Simulador de la API de CumulusMX 5.2.0 para pruebas de extremo a extremo.

Replica cómo el servidor real LEE los cuerpos (según Api.cs, DataEditor.cs y Settings/*.cs): saltarse los 5 primeros
caracteres (`json=`), `WebUtility.UrlDecode` (convierte '+' en espacio), parseo de números con la cultura regional,
comprobación de fechas al editar el dayfile... Así los tests detectan errores de formato que un mock no vería.
Mantiene estado en memoria (ajustes, dayfile, récords) y registra las peticiones recibidas.
"""
import json
import re
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
        self.records: dict[str, dict[str, tuple[float, str]]] = {
            "alltime": {"highTemp": (34.6, "25/07/2019 15:39"), "lowHumidity": (6, "14/09/2024 16:45")}, "monthly": {"7-highTemp": (33.0, "10/07/2019 15:00")},
            "thismonth": {}, "thisyear": {}}
        # logs de intervalo: [nº de línea, DateTime, TS, ...]; datos recientes: [DateTime, TS, ...]; caché MySQL: [clave, sentencia]
        self.datalogs = {False: [[1, "24/08/19 10:00", "1566640800", "10,5", "60"],
                                 [2, "24/08/19 10:10", "1566641400", "10,7", "61"]], True: []}
        self.recent = [self._recent_row("24/08/19 10:00", 1566640800, 10.5), self._recent_row("24/08/19 10:10", 1566641400, 10.7)]
        self.mysqlcache = [[7, "INSERT INTO Monthly VALUES (1)"], [8, "INSERT INTO Monthly VALUES (2)"]]
        self.temp = "10,0" if decimal_comma else "10.0"
        self.rain = {"raintoday": "0.0", "midnightrain": "0.0", "rainratetoday": "0.0"}
        self.created_tables: list[str] = []
        self.sql_posts: list[str] = []

    # ---- helpers
    @staticmethod
    def _recent_row(date: str, ts: int, temp: float) -> list:
        """Fila como la devuelve /api/data/recentdata (RecentData.ToCsv): fecha en texto, marca de tiempo y 26 valores."""
        return [date, ts, 3, 9, 1, 343, 163, temp, 7.7, 1.8, 7.7, 7.7, 7.7, 5.5, 66, 1016.0, 0.0, 0.0, 584.4032, 228, 370, 2.0,
                22.5, 39, -1.0, -1.0, None, None]

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

    def _fmt(self, value: float) -> str:
        return str(value).replace(".", ",") if self.decimal_comma else str(value)

    def _edit_records_route(self, scope: str):
        async def route(request: Request) -> Response:
            body = await self._body(request)
            self.requests.append(("POST", f"/api/edit/{scope}", body))
            text = urllib.parse.unquote(body)                   # Uri.UnescapeDataString: no convierte '+'
            field, txt_value, txt_time = (p.split("=", 1)[1] for p in text.split("&")[:3])
            if field.split("-")[-1] in ("highHumidity", "lowHumidity", "longestDryPeriod", "longestWetPeriod"):
                if not re.fullmatch(r"-?\d+", txt_value):          # int.Parse: "23.0" / "23,0" -> FormatException -> 500
                    return Response("FormatException: input string was not in a correct format", status_code=500)
                value = int(txt_value)
            else:
                # double.Parse con cultura regional: con coma decimal "34.6" se leería como 346 (el punto es separador de miles)
                value = float(txt_value.replace(".", "").replace(",", ".")) if self.decimal_comma else float(txt_value)
            self.records[scope][field] = (value, txt_time.replace("+", " "))
            return PlainTextResponse("Data updated")
        return route

    def _records_get_route(self, scope: str):
        async def route(request: Request) -> Response:
            out: dict[str, str] = {}
            for field, (value, time) in self.records[scope].items():
                out[f"{field}Val"] = str(value) if isinstance(value, int) else self._fmt(value)
                out[f"{field}Time"] = time
            return self._json(out)
        return route

    def _page(self, rows: list, request: Request) -> Response:
        q = request.query_params
        search, start, length = q.get("search[value]", ""), int(q.get("start", 0)), int(q.get("length", 10))
        found = [r for r in rows if search in ",".join(map(str, r))]
        return self._json({"draw": 1, "recordsTotal": len(rows), "recordsFiltered": len(found),
                           "data": found[start:start + length]})

    async def logfile_get(self, request: Request) -> Response:
        return self._page(self.datalogs[False], request)

    async def extralogfile_get(self, request: Request) -> Response:
        return self._page(self.datalogs[True], request)

    async def recent_get(self, request: Request) -> Response:
        return self._page(self.recent, request)

    async def mysqlcache_get(self, request: Request) -> Response:
        return self._page(self.mysqlcache, request)

    async def edit_datalogs(self, request: Request) -> Response:
        body = await self._body(request)
        self.requests.append(("POST", "/api/edit/datalogs", body))
        new = json.loads(body)
        rows = self.datalogs[bool(new.get("extra"))]
        index = new["lines"][0] - 1
        if index >= len(rows) or str(rows[index][2]) != str(new["data"][0][1]):
            return self._json({"errors": {"Logfile": ["TS distinto"]}}, 500)
        if new["action"] == "Edit":
            rows[index] = [index + 1, *new["data"][0]]
        else:
            del rows[index]
        return self._json({"errors": None})

    async def edit_recentdata(self, request: Request) -> Response:
        body = await self._body(request)
        self.requests.append(("POST", "/api/edit/recentdata", body))
        new = json.loads(body)
        row = new["data"][0]
        try:                                                  # RecentData.FromCsvArray: csv[0] es la marca de tiempo (long.Parse)
            timestamp = int(row[0])
        except ValueError:
            return self._json({"errors": {"RecentData": ["Failed to update SQLite"]}}, 500)
        if len(row) < 25:
            return self._json({"errors": {"RecentData": ["Failed to update SQLite"]}}, 500)
        found = next((i for i, r in enumerate(self.recent) if r[1] == timestamp), None)
        if found is None:
            return self._json({"errors": {"Recent": ["no existe"]}}, 500)
        if new["action"] == "Edit":
            def conv(v):
                if v == "null":
                    return None
                try:
                    return int(v)
                except ValueError:
                    return float(v)
            self.recent[found] = [self.recent[found][0], *[conv(v) for v in row]]
        else:
            del self.recent[found]
        return self._json({"errors": None})

    async def edit_mysqlcache(self, request: Request) -> Response:
        body = await self._body(request)
        self.requests.append(("POST", "/api/edit/mysqlcache", body))
        new = json.loads(body)
        if not new.get("statements"):                         # el servidor real itera `statements`: sin ellas da 500
            return self._json({"errors": "statements ausente"}, 500)
        if new["action"] == "Edit":
            for row in self.mysqlcache:
                if row[0] == new["keys"][0]:
                    row[1] = new["statements"][0]
        else:
            self.mysqlcache = [r for r in self.mysqlcache if r[0] not in new["keys"]]
        return self._json({"errors": None})

    async def rain_get(self, request: Request) -> Response:
        return self._json(self.rain)

    async def rain_post(self, request: Request) -> Response:
        body = await self._body(request)
        self.requests.append(("POST", "/api/edit/raintoday", body))
        form = urllib.parse.parse_qs(body)
        # DataEditor.EditRainToday: si falta UNO de los tres campos solo anota un error en su log y responde 200 sin cambiar nada
        if all(k in form and re.fullmatch(r"-?\d+(\.\d+)?", form[k][0]) for k in ("raintoday", "midnightrain", "rainratetoday")):
            self.rain = {k: f"{float(form[k][0]):.1f}" for k in ("raintoday", "midnightrain", "rainratetoday")}
        return self._json({"raintoday": self.rain["raintoday"], "raincounter": "0.0", "startofdayrain": "0.0", "rainmult": "1.000"})

    async def version(self, request: Request) -> Response:
        return self._json({"Version": "5.2.0", "Build": "5026"})

    def build(self) -> Starlette:
        r = [
            Route("/api/settings/{req}", self.get_settings),
            Route("/api/setsettings/{req}", self.set_settings, methods=["POST"]),
            Route("/api/tags/process.json", self.tags),
            Route("/api/data/dayfile", self.dayfile_get),
            Route("/api/edit/dayfile", self.edit_dayfile, methods=["POST"]),
            Route("/api/data/logfile", self.logfile_get),
            Route("/api/data/extralogfile", self.extralogfile_get),
            Route("/api/data/recentdata", self.recent_get),
            Route("/api/data/mysqlcache.json", self.mysqlcache_get),
            Route("/api/edit/datalogs", self.edit_datalogs, methods=["POST"]),
            Route("/api/edit/recentdata", self.edit_recentdata, methods=["POST"]),
            Route("/api/edit/mysqlcache", self.edit_mysqlcache, methods=["POST"]),
            Route("/api/info/version.json", self.version),
            Route("/api/edit/raintodayeditdata.json", self.rain_get),
            Route("/api/edit/raintoday", self.rain_post, methods=["POST"]),
        ]
        for scope in ("alltime", "monthly", "thismonth", "thisyear"):
            r.append(Route(f"/api/edit/{scope}", self._edit_records_route(scope), methods=["POST"]))
            r.append(Route(f"/api/edit/{scope}records.json", self._records_get_route(scope)))
        return Starlette(routes=r)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.build()), base_url="http://cumulus")
