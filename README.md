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

```bash
docker run -d \
  -e MCP_AUTH_TOKEN=your-secret-token \
  -e CUMULUSMX_URL=http://your-cumulusmx-host:8998 \
  -p 8000:8000 \
  edudele/cumulusmx-mcp
```

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
| `CUMULUS_TIMEOUT` | | `30` | Timeout in seconds for each request to CumulusMX |
| `CUMULUS_RETRIES` | | `2` | Retries (exponential backoff) of GET requests on connection errors, timeouts and HTTP 502/504. POST requests are never retried |
| `CUMULUS_USER` | | — | HTTP Basic Auth username for CumulusMX API (optional) |
| `CUMULUS_PASSWORD` | | — | HTTP Basic Auth password for CumulusMX API (optional) |
| `ENABLE_WRITE_TOOLS` | | `true` | Publish the 15 tools that modify data or station state. With `false` they are not even listed to the client (read-only server) |
| `LOG_LEVEL` | | `info` | Log verbosity: `info`, `debug`, `warning`, `error` |

> **Security tip:** write tools are enabled by default. If you only need to query your station, set
> `ENABLE_WRITE_TOOLS=false`: the 15 write tools are then **not published at all** (clients only see the 43 read-only
> tools), and they would refuse to run even if called directly.

---

## Health check

An unauthenticated health check endpoint is exposed at `http://localhost:8000/health` (or `/health` on your host/proxy). It returns:

```json
{"status": "healthy", "service": "cumulusmx-mcp", "version": "1.5.0"}
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

## Versioning and releases

The project follows [Semantic Versioning](https://semver.org/) and keeps a [CHANGELOG](CHANGELOG.md).
The current version is defined once in `server.py` (`__version__`) and is reported by `GET /health`,
the `server_status` tool and the startup log.

To cut a release:

1. Update `__version__` in `server.py` and add a `## [X.Y.Z]` section at the top of `CHANGELOG.md`
   (`tests/test_versioning.py` fails if they differ). Also update `metadata.version` in the skill's `SKILL.md`.
2. Merge to `main` (publishes the `latest` Docker image).
3. Tag the release: `git tag vX.Y.Z && git push origin vX.Y.Z`. The workflow checks that the tag matches
   `__version__` and publishes `X.Y.Z`, `X.Y`, `X` and `latest` images to Docker Hub.

Tests run on every pull request and on pushes to any branch except `main` (`.github/workflows/tests.yml`):

```bash
pip install "fastmcp>=3,<5" httpx pytest pytest-asyncio
MCP_AUTH_TOKEN=test python -m pytest -q
```

---

## Building locally

```bash
git clone https://github.com/edudele/cumulusmx-mcp.git
cd cumulusmx-mcp
docker build -t cumulusmx-mcp .
```

---

## Docker Hub

[edudele/cumulusmx-mcp](https://hub.docker.com/r/edudele/cumulusmx-mcp)

Images are built and pushed automatically on every push to `main`.

---

## License

MIT
