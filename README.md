# cumulusmx-mcp

[![Build and push to Docker Hub](https://github.com/edudele/cumulusmx-mcp/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/edudele/cumulusmx-mcp/actions/workflows/docker-publish.yml)
[![Docker Pulls](https://img.shields.io/docker/pulls/edudele/cumulusmx-mcp)](https://hub.docker.com/r/edudele/cumulusmx-mcp)
[![Docker Image Size](https://img.shields.io/docker/image-size/edudele/cumulusmx-mcp/latest)](https://hub.docker.com/r/edudele/cumulusmx-mcp)

MCP server that exposes a [CumulusMX](https://cumuluswiki.org/a/CumulusMX) weather station to any LLM via the [Model Context Protocol](https://modelcontextprotocol.io/).

Built with [FastMCP](https://github.com/jlowin/fastmcp) and deployed as a Docker container. Designed to sit behind a reverse proxy (e.g. [Pangolin](https://github.com/fosrl/pangolin)) so your station is never exposed directly.

---

## Features

- **Current conditions** — temperature, humidity, pressure, wind, rain, solar/UV in real time.
- **Today / yesterday** — highs, lows and times for all variables.
- **Historical data & Analytics** — daily summaries from dayfile across all years, flexible column alias filtering (`date`, `mintemp`, `rain`, etc.) and aggregated statistical summaries (`summary_only`).
- **Agronomy & climate tools** — `climatology` (monthly normals), `frost_dates` (last spring / first autumn frost, frost-free period), `degree_days` (growing degree days), `wind_rose` (16-point rose) and `daily_briefing` (live conditions + yesterday in context + month vs normal + streaks in progress).
- **Full CumulusMX API coverage** — read *and* write: every settings section (`get_settings` / `update_settings` with merge, `dry_run` preview and no blind writes), dedicated **MySQL** and **MQTT** tools (`get_/update_mysql_settings`, `mysql_table`, `get_/update_mqtt_settings`, `mysql_cache`), history corrections (`edit_dayfile`, `edit_datalog`, `edit_recent_data`, `edit_records`), diary import/export, `interval_data` / `daily_data`, `records_query`, custom charts, WebSocket `live_sample` and HTTP station input. See [API coverage](#api-coverage).
- **Safety net for the write tools** — every write is **audited** (log + optional JSON-Lines file, secrets masked), the previous state is **backed up automatically** and can be undone with `restore_backup`, and an optional **read-only token** lets others query the station without being able to change anything (it does not even see the write tools). See [Security model](#security-model).
- **Alerts and reports** — `weather_alerts` (frost, wind, rain, heat, UV, pressure drop...) and `period_report` (a shareable Markdown weekly / monthly / yearly report compared with the station's own normals).
- **Observable** — `GET /metrics` exposes Prometheus metrics: tool calls and latency, writes by outcome, requests to CumulusMX, whether the station is reachable, stored backups. See [Monitoring](#monitoring).
- **Runs anywhere** — Docker, `pip`/`uvx`, or **stdio** for local clients such as Claude Desktop. Pinned dependencies, tested on FastMCP 3.x and 4.x and Python 3.10 - 3.13.
- **Resilient** — GET requests to CumulusMX are retried with backoff on transient failures; timeouts are configurable.
- **Agent-ready** — the server sends usage instructions to MCP clients (which tool for which question, units, decimal conventions, write-tool etiquette).
- **Climate analytics** — `count_days`, `find_streaks`, `anomaly` and `data_quality` answer "how many days above 35 °C?", "longest dry spell?", "was this July warmer than normal?" and "can I trust the history?" from the station's own data, with no extra CumulusMX endpoints.
- **Historical rankings & Annual records** — competition rankings (1, 2, 2, 4) for extremes (heat, cold, rain, wind gusts) and yearly records summaries.
- **MCP Resources** — declarative `weather://` resources for direct context retrieval in MCP clients.
- **MCP Prompts** — ready-to-use guided prompts for daily analysis, extreme events, annual summaries, and period comparisons.
- **Records** — all-time, this year, this month, by calendar month or custom date range.
- **Graphs** — recent and daily graph data series with statistical downsampling (`graph_summary`).
- **NOAA reports** — monthly and annual plain-text reports.
- **Webtags** — query any CumulusMX webtag by name or evaluate webtag templates.
- **Health check** — unauthenticated `/health` endpoint for Docker and monitoring orchestrators.
- **Bearer token auth** — all MCP endpoints require `Authorization: Bearer ***`.
- **Prefix stripping** — configurable middleware for reverse proxies that don't rewrite paths.
- **Agent skill** — ready-to-use [`skills/cumulusmx-weather`](skills/cumulusmx-weather/SKILL.md) that teaches an agent when and how to use these tools (see [Agent skill](#agent-skill)).
- **MCP Tool Annotations** — full `readOnlyHint` and `destructiveHint` annotations for client permission grouping (read-only vs write/delete in Claude and other clients).

---

## Quick start

### Docker

The image is multi-architecture (`linux/amd64` and `linux/arm64`), so it runs the same on a PC, a NAS or a Raspberry Pi.

```bash
docker run -d \
  -e MCP_AUTH_TOKEN=your-secret-token \
  -e CUMULUSMX_URL=http://your-cumulusmx-host:8998 \
  -p 8000:8000 \
  edudele/cumulusmx-mcp
```

### Python package (pip / uvx) and stdio

```bash
pip install cumulusmx-mcp      # or: uvx cumulusmx-mcp   (development version: pip install git+https://github.com/edudele/cumulusmx-mcp)
CUMULUSMX_URL=http://your-cumulusmx-host:8998 MCP_AUTH_TOKEN=secret cumulusmx-mcp           # HTTP on :8000
cumulusmx-mcp --transport stdio                                                          # local MCP client (no HTTP, no token)
```

Claude Desktop (or any stdio client):

```json
{
  "mcpServers": {
    "cumulusmx": {
      "command": "cumulusmx-mcp",
      "args": ["--transport", "stdio"],
      "env": { "CUMULUSMX_URL": "http://192.168.1.10:8998" }
    }
  }
}
```

In stdio mode the process is started by your client and nobody else can reach it, so there is no Bearer token.

### Docker Compose

```yaml
services:
  cumulusmx-mcp:
    image: edudele/cumulusmx-mcp
    environment:
      MCP_AUTH_TOKEN: your-secret-token
      CUMULUSMX_URL: http://cumulusmx:8998   # adjust to your CumulusMX host
      PORT: 8000
      MCP_STRIP_PREFIX: ""                   # set if behind a prefix-routing proxy
      CUMULUS_DATA_START_YEAR: 2023          # start year for historical rankings
      CUMULUS_FROST_THRESHOLD: 0.0           # threshold in °C for frost day counts
      CUMULUS_CACHE_TTL: 600                 # dayfile cache TTL in seconds
    ports:
      - "8000:8000"
    restart: unless-stopped
```

See [`.env.example`](.env.example) for all variables.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `MCP_AUTH_TOKEN` | ✅ | — | Bearer token clients must send |
| `CUMULUSMX_URL` | | `http://cumulusmx:8998` | Base URL of the CumulusMX HTTP API |
| `PORT` | | `8000` | Port the server listens on |
| `MCP_STRIP_PREFIX` | | `/mcp-res` | Path prefix to strip (set to `""` to disable) |
| `CUMULUS_DATA_START_YEAR` | | `2023` | First year of station data for caching and default rankings range |
| `CUMULUS_FROST_THRESHOLD` | | `0.0` | Temperature threshold (°C) to count frost days |
| `CUMULUS_CACHE_TTL` | | `600` | In-memory cache TTL in seconds for current year dayfile data |
| `CUMULUS_TIMEZONE` | | — | IANA time zone of the station (e.g. `Europe/Madrid`). Needed only to give readable dates (`2025-01-31 14:00`) to `interval_data` / `daily_data`; Unix seconds always work |
| `CUMULUS_MASK_SECRETS` | | `false` | `true` masks passwords and keys (`pass`, `password`, `phpsecret`, API keys...) in `get_settings`, `get_mysql_settings` and `get_mqtt_settings`. Off by default: the server returns the configuration as CumulusMX does |
| `MCP_METRICS` | | `true` | Serves Prometheus metrics at `GET /metrics` (same Bearer token as `/mcp`). `false` removes the endpoint |
| `MCP_AUTH_TOKEN_READONLY` | | — | Second Bearer token that can **query but not write**: write tools refuse to run with it and settings are returned with passwords masked |
| `CUMULUS_BACKUP_DIR` | | system temp dir | Where the pre-write backups are kept (`none` disables them). Mount a volume (the image uses `/data/backups`) to keep them across restarts |
| `CUMULUS_BACKUP_KEEP` | | `200` | Maximum number of backups kept (the oldest are deleted) |
| `CUMULUS_AUDIT_FILE` | | — | Also append every write (JSON Lines) to this file; the audit trail always goes to the log (logger `cumulusmx-mcp.audit`). The image uses `/data/audit.jsonl` |
| `CUMULUS_TIMEOUT` | | `30` | Timeout in seconds for each request to CumulusMX |
| `CUMULUS_RETRIES` | | `2` | Retries (exponential backoff) of GET requests on connection errors, timeouts and HTTP 502/504. POST requests are never retried |
| `CUMULUS_USER` | | — | HTTP Basic Auth username for CumulusMX API (optional) |
| `CUMULUS_PASSWORD` | | — | HTTP Basic Auth password for CumulusMX API (optional) |
| `ENABLE_WRITE_TOOLS` | | `true` | Publish the 29 tools that modify data or station state. With `false` they are not even listed to the client (read-only server) |
| `LOG_LEVEL` | | `info` | Log verbosity: `info`, `debug`, `warning`, `error` |

> **Security tip:** write tools are enabled by default. If you only need to query your station, set
> `ENABLE_WRITE_TOOLS=false`: the 29 write tools are then **not published at all** (clients only see the 60 read-only
> tools), and they would refuse to run even if called directly.

---

## Security model

| Layer | What it does |
|---|---|
| **Bearer token** (`MCP_AUTH_TOKEN`) | Required on every request except `/health`. Full access. |
| **Read-only token** (`MCP_AUTH_TOKEN_READONLY`) | Optional second token. It can use every read tool; `tools/list` does **not** include the write tools for it, any attempt to run one is refused (*"este token es de solo lectura"*) and audited, and settings come back with passwords masked. |
| **`ENABLE_WRITE_TOOLS=false`** | The 29 write tools are not published at all. |
| **Audit** | Each write is recorded (tool, token scope, arguments with secrets masked, result `ok` / `fallo` / `denegado` / `error`) in the log and, with `CUMULUS_AUDIT_FILE`, in a JSON-Lines file. `get_audit_log` shows the recent ones. |
| **Backups and undo** | Before changing a settings section, a day-file line, an interval-log line, a recent-data record, a MySQL-cache statement or a record, the previous state is saved (`CUMULUS_BACKUP_DIR`, files `0600`). `list_backups` / `get_backup` / `restore_backup` undo it, and a restore is itself undoable. |
| **Metrics** | `GET /metrics` (Prometheus text format) needs a Bearer token, unlike `/health`. It exposes counters and durations only (tool names and outcomes, requests to CumulusMX), never arguments or station data. |
| **No blind writes** | Settings updates read the current section first and refuse to write if they cannot (the old classic updaters used to overwrite with only the fragment). `dry_run=true` previews the diff. |
| **Secrets** | Passwords are returned by the settings tools unless `CUMULUS_MASK_SECRETS=true` or the read-only token is used. They never appear in the audit log. Backups can contain them, hence the `0600` permissions. |
| **No outbound calls** | The server only talks to CumulusMX. FastMCP's update check against PyPI is disabled by default. |

Keep the server behind HTTPS (a reverse proxy) when it is reachable from outside your network.

---

## Health check

An unauthenticated health check endpoint is exposed at `http://localhost:8000/health` (or `/health` on your host/proxy). It returns:

```json
{"status": "healthy", "service": "cumulusmx-mcp", "version": "1.8.2"}
```

The Docker container includes a native `HEALTHCHECK` directive verifying this endpoint with Python's standard `urllib.request`.

---

## Reverse proxy

The server is designed to work with proxies that use **path-prefix routing** (e.g. Pangolin with *clip prefix* / rewrite mode). Set `MCP_STRIP_PREFIX` to the prefix your proxy uses so the server sees clean paths.

Example with Pangolin routing `https://your-domain.com/mcp-res` → `http://cumulusmx-mcp:8000`:

```
MCP_STRIP_PREFIX=/mcp-res
```

---

## MCP endpoint

```
https://your-domain.com/mcp-res/mcp
```

Add it to your MCP client (e.g. Hermes, Claude Desktop) with:

```json
{
  "mcpServers": {
    "cumulusmx": {
      "url": "https://your-domain.com/mcp-res/mcp",
      "headers": {
        "Authorization": "Bearer your-secret-token"
      }
    }
  }
}
```

---

## Available tools

| Tool | Description |
|---|---|
| `ping` | Fast check that the MCP server responds |
| `server_status` | MCP server and CumulusMX version diagnostic |
| `station_info` | Version, units, alarms, time format, display settings |
| `current_data` | Full current conditions (JSON) |
| `recent_data` | Recent log entries (last hours) |
| `today_yesterday` | Today/yesterday highs & lows by variable (`temp`, `hum`, `rain`, `wind`, `pressure`, `solar`) |
| `today_all` | All today/yesterday variables in one single call |
| `graph_data` | Recent graph series (~24 h) |
| `daily_graph_data` | Daily graph series (all history) |
| `graph_summary` | Statistical summary of a graph series (min, max, avg, last, optional downsampled series) |
| `dayfile` | Paginated daily history (`dayfile.txt`) |
| `dayfile_range` | Daily history filtered by date range, with column alias support and `summary_only` mode |
| `rankings` | Historical competition rankings (1, 2, 2, 4) of extremes (`temp_min`, `temp_max`, `lluvia_total`, `racha_max`, `temp_media`, `horas_sol`) |
| `year_records` | Annual records and stats (extremes, rain, frost/warm days) for a given year |
| `count_days` | Count days matching a condition (e.g. `temp_max >= 35`) with a per-year breakdown and optional date list |
| `find_streaks` | Longest runs of consecutive days matching a condition (dry spells, heatwaves, frost runs) and any run in progress |
| `anomaly` | Compare a period with the station's own climatology (same calendar window in other years): normal, difference, percentile, rank |
| `data_quality` | Audit the daily history: missing days, days without temperature, incoherent values |
| `climatology` | Monthly normals (Jan-Dec): mean temperatures, rain, rain days, sun hours, extremes and record years |
| `frost_dates` | Per year: last spring frost, first autumn frost, frost days and frost-free period; average dates |
| `degree_days` | Growing degree days (base/upper temperature, `average` or `modified` method) per month and year |
| `wind_rose` | Days per dominant wind direction (16 sectors) and peak gust per gust direction |
| `daily_briefing` | One-call daily overview: now, yesterday vs same month in other years, month-to-date anomaly, streaks in progress |
| `logfile` | Interval log for a date range (standard or extra sensors) |
| `records` | Records by scope (`alltime`, `thismonth`, `thisyear`, `month`) and variable |
| `records_all` | All record categories in one single call |
| `records_period` | Records calculated for a custom date range |
| `noaa_report` | NOAA monthly or annual plain-text report |
| `monthly_summary` | Month-by-month climate summary for a given year |
| `climate_summary` | Multi-day or multi-year aggregate climate summary |
| `compare_periods` | Side-by-side comparison of two date ranges |
| `snapshot` | Quick station snapshot (temperatures, extremes, wind, rain, sun/moon, status) |
| `get_tags` | Query CumulusMX webtags by name; numbers use a decimal point (`rc` format, optional) |
| `render_template` | Evaluate custom webtag templates with formatting modifiers |
| `diary` | Weather diary entries or summary |
| `simple_report` | CumulusMX summary reports over the whole history: temperature, rainfall, wind run, sunshine, ET, dry/wet days |
| `extra_sensors` | Current values of extra sensors: soil, leaf wetness, AirLink air quality, lightning, CO2, laser depth, snow... |
| `api_get` | Low-level whitelisted read-only access to CumulusMX GET endpoints |

### System & Maintenance (/utils/*)

| Tool | Description |
|---|---|
| `reloaddayfile` | Reload `dayfile.txt` on CumulusMX and invalidate local in-memory dayfile cache |
| `clear_alarms` | Reset a specific alarm (e.g. `BatteryLow`, `Sensor`) or all alarms (`alarm="all"`) |
| `clear_error_log` | Clear the accumulated error log in CumulusMX |
| `trigger_ftp` | Trigger an immediate FTP / web upload for daily graphs, NOAA reports, graphs, or logfiles |
| `purgemysql` | Purge pending/failed command queue and MySQL cache |

### Reports & Upload (/genreports/*, /uploadreport/*)

| Tool | Description |
|---|---|
| `generate_reports` | Generate annual or monthly NOAA reports on the server (`year` or `month`) |
| `upload_reports` | Upload annual or monthly NOAA reports via configured FTP/web transfer |

### Diagnostics (/data/errorlog.json)

| Tool | Description |
|---|---|
| `get_error_log` | Retrieve system warning and error log entries |

### Alerts, reports and safety net

| Tool | Description |
|---|---|
| `weather_alerts` | Active alerts right now: frost (today / in progress / night risk), strong wind, intense rain, heat, high UV, fast pressure drop, very dry hot air, station without data. Thresholds can be overridden |
| `period_report` | Shareable Markdown report for a week, month or year: temperatures, rain and streaks, wind, pressure, sun and a comparison with the station's own normals |
| `list_backups` / `get_backup` | Backups taken before each write (settings, day-file and interval-log lines, recent data, MySQL cache, records) |
| `restore_backup` | Undo any *edit* from its backup: settings, day-file / interval-log lines, recent data, MySQL-cache statements and records (`dry_run=true` by default; the restore is undoable too). Deletions cannot be re-inserted through CumulusMX's API; their backup keeps the content |
| `get_audit_log` | Recent writes done through this server |

### Configuration, MySQL & MQTT

| Tool | Description |
|---|---|
| `get_settings` | Read any of the 18 settings sections: `program`, `station`, `internet`, `thirdparty`, `extrasensors`, `extrawebfiles`, `calibration`, `language`, `noaa`, `mysql`, `alarms`, `useralarms`, `wizard`, `customlogs_interval`, `customlogs_daily`, `display`, `httpfiles`, `mqtt` |
| `update_settings` | Change any section: reads the current one, deep-merges only your fields (or `replace=true`), sends it in the format the endpoint expects. `dry_run=true` shows the diff without applying. Never writes if the current settings cannot be read |
| `get_mysql_settings` / `update_mysql_settings` | MySQL connection, monthly/dayfile/realtime tables, retention and custom commands |
| `mysql_table` | Create or update the `monthly`, `dayfile` or `realtime` MySQL table |
| `mysql_cache` / `edit_mysql_cache` | Inspect and edit/delete the pending SQL commands that failed to reach MySQL |
| `get_mqtt_settings` / `update_mqtt_settings` | MQTT broker, credentials, TLS, topics and templates |
| `update_noaa_settings` | NOAA report settings (counterpart of `get_noaa_settings`) |

### Data access, queries and real time

| Tool | Description |
|---|---|
| `interval_data` | Interval logs for a range with only the fields you ask for (main log and extra-sensor log), no paging |
| `daily_data` | Day-file fields by their official CumulusMX names for a range |
| `records_query` | Day-file query computed by CumulusMX (min/max/sum/avg/count, optional condition and period) |
| `graph_select` | Custom "Select-a-Chart" / "Select-a-Period" series for a date range |
| `live_sample` | Listen to the `/ws/` WebSocket for a few seconds and return the live messages |
| `records_edit_data` | Record-editing screen data (current values, or candidates from the day file / logs) |
| `diary_export` | Whole diary as CSV |
| `get_current_conditions_text` | Current-conditions text |

### History corrections, diary and device input (write)

| Tool | Description |
|---|---|
| `edit_dayfile` | Edit or delete one `dayfile.txt` line (date-checked; updates SQLite and MySQL if enabled) |
| `edit_datalog` | Edit or delete one line of a monthly interval log (or extra-sensor log) |
| `edit_recent_data` | Edit or delete a row of the recent-data table |
| `edit_records` | Correct an all-time, monthly, this-month or this-year record (sends the value with the station's decimal separator) |
| `diary_upload` / `diary_automate` | Import diary entries from CSV / choose the laser snow sensor that fills the diary |
| `station_input` | Send a reading to the HTTP station endpoints (`ecowitt`, `json`, `wunderground`, `ambient`...) to test a station setup |
| `generate_reports` | Also accepts `report_type="all"` to generate every missing report |

> **Back up before editing history.** The `edit_*` tools rewrite `dayfile.txt`, log files and record files.

### Data Editing (/edit/*)

| Tool | Description |
|---|---|
| `edit_current_conditions` | Update current weather condition text description |
| `edit_rain_today` | Adjust today's rain accumulation and midnight rain values |
| `edit_diary` | Create or update a weather diary entry with observations and snow/storm flags |
| `delete_diary` | Delete a weather diary entry for a specific date |

### Configuration (/settings/*, /setsettings/*)

| Tool | Description |
|---|---|
| `get_station_settings` | Get station configuration (Alpaca schema) |
| `update_station_settings` | Update station configuration settings (partial merge supported) |
| `get_calibration_settings` | Get sensor calibration offsets and multipliers |
| `update_calibration_settings` | Update sensor calibration factors and offsets |
| `get_alarm_settings` | Get alarm limits and notification configuration |
| `update_alarm_settings` | Update alarm thresholds and notification settings |
| `get_noaa_settings` | Get NOAA report generation configuration |
| `test_email` | Send a test email to verify alarm SMTP settings |

---

## API coverage

Checked against the `Api.cs` controllers of CumulusMX 5.2.0 (217 named endpoints):

| Area | Status |
|---|---|
| Data, logs, diary, graphs, records, today/yesterday, extra sensors, tags, reports, info | Covered (read) |
| Settings: all 18 sections, read and write | Covered (`get_settings`, `update_settings` + dedicated MySQL/MQTT/NOAA tools) |
| MySQL tables (`create*sql`, `update*sql`) and failed-command cache | Covered |
| Utilities (reload day file, purge MySQL, FTP now, clear alarms/error log), report generation and upload | Covered |
| Data editor: day file, interval logs, recent data, MySQL cache, records, rain today, current conditions, diary | Covered |
| `intervaldata` / `dailydata`, `records/query`, `selectachart` / `selectaperiod`, WebSocket `/ws/` | Covered (`interval_data`, `daily_data`, `records_query`, `graph_select`, `live_sample`) |
| HTTP station input (`/station/ecowitt`, `json`, `wunderground`, `ambient`...) | Covered (`station_input`) |
| Web dashboard pages, static assets and localisation files (`/`, `/js`, `/json`, `/ai2`) | Not exposed: they are the CumulusMX web UI, not data |

The write operations were verified against the CumulusMX source and with mocked tests; they are not exercised against a
live station by the test suite. Settings and editors take effect on the real station, so use `dry_run` for settings and keep backups
before editing history.

---

## MCP Resources

FastMCP declarative resources available for direct context reading:

| Resource URI | Description |
|---|---|
| `weather://current` | Real-time current weather conditions |
| `weather://today` | Summary of today and yesterday extremes and values |
| `weather://station` | Station info, version and units configuration |
| `weather://records/alltime` | All-time historical records of the station |

---

## MCP Prompts

Ready-to-use guided prompts for AI assistants:

| Prompt | Arguments | Description |
|---|---|---|
| `resumen_diario` | `date` (optional) | Daily weather report and anomaly analysis |
| `analisis_extremos` | `variable`, `top` | Historical extreme events comparison and ranking analysis |
| `comparativa_periodos` | `start_a`, `end_a`, `start_b`, `end_b` | Period A vs Period B climate comparison |
| `resumen_anual` | `year` | Comprehensive annual climate summary and milestone review |
| `resumen_mensual` | `year`, `month` | Month report versus the station's own climatology |
| `heladas_y_cultivo` | `base_temp` (optional) | Frost dates and growing degree days for garden planning |
| `auditoria_datos` | — | Data-quality audit of the history before analysing it |

---

## Agent skill

The repository ships an agent skill in [`skills/cumulusmx-weather/`](skills/cumulusmx-weather/SKILL.md).
The MCP server provides the tools; the skill tells the agent **when** to use each one (for example `rankings` for
"hottest day ever", `graph_summary` instead of dumping thousands of points) and how to treat the write tools
(always ask before changing the station).

To use it with Claude Code, copy the folder into your skills directory:

```bash
cp -r skills/cumulusmx-weather ~/.claude/skills/
```

Other agents that support `SKILL.md` folders can load it from the same directory. The skill requires this MCP server to be connected.

---

## Development

```bash
git clone https://github.com/edudele/cumulusmx-mcp.git && cd cumulusmx-mcp
pip install -e ".[dev]"
python -m pytest -q            # ~370 tests, a few seconds; no CumulusMX needed (a simulator is included)
ruff check .
```

The code lives in the `cumulusmx_mcp/` package, split by responsibility (config, HTTP client, day-file analytics,
settings engine, one module of tools per domain...). `server.py` is only a compatibility shim.
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** explains the layers, the rules the tests enforce and how to add a tool;
[CONTRIBUTING.md](CONTRIBUTING.md) has the checklist. To smoke-test a deployed server (read-only calls only):

```bash
python scripts/smoke_readonly.py https://your-domain/mcp-res/mcp YOUR_TOKEN
```

---

## Monitoring

`GET /health` (no token) is the liveness check. `GET /metrics` returns Prometheus metrics and needs the same Bearer token
(the read-only token is enough). Example scrape configuration:

```yaml
scrape_configs:
  - job_name: cumulusmx-mcp
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials: <MCP_AUTH_TOKEN_READONLY>
    static_configs:
      - targets: ["cumulusmx-mcp:8000"]
```

| Metric | Type | Meaning |
|---|---|---|
| `cumulusmx_mcp_info{version}` | gauge | Server version (always 1) |
| `cumulusmx_mcp_tool_calls_total{tool,resultado}` | counter | Tool calls (`ok` / `error`) |
| `cumulusmx_mcp_tool_duration_seconds{tool}` | histogram | Tool call latency |
| `cumulusmx_mcp_writes_total{tool,resultado}` | counter | Audited writes (`ok` / `fallo` / `denegado` / `error`) |
| `cumulusmx_mcp_cumulus_requests_total{method,resultado}` | counter | Requests to CumulusMX (`ok` / `http_error` / `conexion`) |
| `cumulusmx_mcp_cumulus_request_duration_seconds{method}` | histogram | CumulusMX request latency |
| `cumulusmx_mcp_cumulus_up` | gauge | 1 if the last request to CumulusMX worked |
| `cumulusmx_mcp_cumulus_last_success_timestamp_seconds` | gauge | Last successful request to CumulusMX |
| `cumulusmx_mcp_backups_stored` | gauge | Backups kept in `CUMULUS_BACKUP_DIR` |
| `cumulusmx_mcp_write_tools_enabled` | gauge | 1 if write tools are enabled |

Useful alerts: `cumulusmx_mcp_cumulus_up == 0` for 5 minutes (station or network down),
`increase(cumulusmx_mcp_writes_total{resultado="denegado"}[1h]) > 0` (someone tried to write with a read-only token).
Counters live in memory and restart from zero with the process, which Prometheus handles natively.

---

## Versioning and releases

The project follows [Semantic Versioning](https://semver.org/) and keeps a [CHANGELOG](CHANGELOG.md).
The version is defined once, in `cumulusmx_mcp/__init__.py` (`__version__`), and is reported by `GET /health`,
the `server_status` tool, `cumulusmx-mcp --version` and the startup log.

To cut a release:

1. Update `__version__` and add a `## [X.Y.Z]` section at the top of `CHANGELOG.md`; also update `metadata.version` in the
   skill's `SKILL.md` (`tests/test_versioning.py` fails if they differ).
2. Merge to `main` (publishes the `latest` Docker image).
3. Run the **Release** workflow (Actions -> Release -> Run workflow, version `X.Y.Z`), or push the tag yourself
   (`git tag vX.Y.Z && git push origin vX.Y.Z`). It validates the version, creates the tag and the GitHub release with the
   CHANGELOG notes, publishes the `X.Y.Z`, `X.Y`, `X` and `latest` multi-architecture images to Docker Hub and, once
   PyPI trusted publishing is set up (see [CONTRIBUTING.md](CONTRIBUTING.md)), uploads the package to PyPI.

CI (`.github/workflows/tests.yml`) runs on every pull request and on pushes to any branch except `main`: lint, the test
suite on Python 3.10 / 3.12 / 3.13 with FastMCP 3.x and 4.x (coverage must stay above 90 %), a build check of the
package, smoke tests of the Docker image (amd64 and emulated arm64) and an informational dependency audit. Dependencies are pinned in `requirements.txt` and kept up to date by Dependabot.

---

## Building locally

```bash
docker build -t cumulusmx-mcp .
docker run -d -p 8000:8000 -e MCP_AUTH_TOKEN=secret -e CUMULUSMX_URL=http://cumulusmx:8998 -v cumulus-mcp:/data cumulusmx-mcp
```

---

## Docker Hub

[edudele/cumulusmx-mcp](https://hub.docker.com/r/edudele/cumulusmx-mcp)

Images are built and pushed automatically on every push to `main`.

---

## License

MIT
