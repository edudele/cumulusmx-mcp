# Architecture

`cumulusmx-mcp` is a thin, well-tested layer between an MCP client (an AI agent) and the HTTP API of a CumulusMX weather
station. Almost all of the value is in three things: translating questions into the right API calls, computing climate
statistics from the day file, and making *writes* safe.

## Layers

Each module may import only modules **above it in this list** (a test enforces it). `tools/*` sit on top of the core.

```
config            environment variables and constants (read once)
utils             pure helpers: dates, numbers, dict merge, secret masking
metrics           Prometheus counters/histograms/gauges (exposed at GET /metrics by `app`)
safety            write guard (ENABLE_WRITE_TOOLS, token scope) and audit trail
backups           pre-write backups and their restoration
client            HTTP client to CumulusMX: basic auth, retries, response clipping (valid JSON envelope when it truncates)
capture           reads the previous state of what is about to be edited (for the backups)
app               the FastMCP instance, ASGI middlewares (prefix, Bearer), @write_tool
dayrows           day-file rows: columns, in-memory cache, basic statistics
analysis          pure climate calculations over day-file rows (streaks, anomalies, frost, degree days...)
settings_engine   generic read-merge-write for the 18 settings sections
alerts, reporting pure logic of weather_alerts and period_report
tools/            the MCP tools, one module per domain (observations, graphs, records, climate, reports,
                  maintenance, diary, settings, editors, alerts, period_report, safety_tools)
resources, prompts MCP resources (weather://...) and guided prompts
cli               start-up: HTTP (uvicorn) or stdio
```

Pure logic (`analysis`, `alerts`, `reporting`, `utils`) takes rows/dicts and returns values: no network, easy to test.
The tools are thin: validate arguments, call `client`, call the pure logic, `json.dumps` the result.

## Rules (enforced by `tests/test_architecture.py`)

1. **Reference other modules through their module**: `client._get(...)`, `config.BASE`, `dayrows._all_dayfile()`.
   Never `from .client import _get`: that copies the function and a test (or an extension) that patches
   `client._get` would silently not affect you. The only allowed bare imports are `mcp`, `READ_ONLY`, `WRITE_ACTION`,
   `write_tool`, `logger` and the two entry points.
2. **A public name is defined in exactly one module.** The `server` compatibility facade resolves names that way.
3. **Never import from a higher layer.**
4. **Every tool has a useful description** (the docstring is what the model reads to choose and use the tool).

## Writing a tool

```python
# cumulusmx_mcp/tools/climate.py  (or the module that fits the domain)
from ..app import READ_ONLY, mcp, write_tool

@mcp.tool(annotations=READ_ONLY)                 # read-only tool
async def my_tool(year: int) -> str:
    """What it does, when to use it, units and gotchas. The model reads this."""
    rows = await dayrows._all_dayfile()
    return json.dumps(analysis.my_calculation(rows, year), ensure_ascii=False)

@write_tool                                       # anything that changes the station or its files
async def my_write_tool(...) -> str:
    ...
```

* Put the calculation in `analysis.py` (pure, takes rows) and test it with synthetic rows.
* **Write tools** go through `@write_tool`, which registers them only if `ENABLE_WRITE_TOOLS` allows, checks the token scope
  and audits every call. Do not repeat those checks inside the tool. If the tool changes state that can be recovered, save it
  first with `backups.save(kind, target, payload, tool)` and return the `copia_id`.
* Return JSON strings for structured data (`ensure_ascii=False`), plain text for reports.
* New module? Add it to `tools/__init__.py` and to `_MODULES` in `_facade.py`.
* Document it: `skills/cumulusmx-weather/reference.md` (a test fails if a tool is missing), the README table, `CHANGELOG.md`.

## Where numbers come from (units, decimals, no-data)

* The day file columns are in the station's units; the Spanish names are fixed in `dayrows.DAYFILE_COLS` (the order is the
  official `DayfileFieldNames` order of CumulusMX 5.2.0 and a test checks it).
* Text that CumulusMX formats itself follows the station's regional settings (`"10,0"`). `get_tags`/`snapshot` request tags with
  `rc` to get plain numbers. `utils._num` converts and treats `-9999` / `9999` as "no data".

## Tests

| File | What it covers |
|---|---|
| `test_server.py`, `test_analysis.py`, `test_power_tools.py`, `test_official_docs.py`, `test_full_api.py` | Tools and logic with mocks. They use `import server` (the facade) and `patch.object(server, "_get", ...)`: the facade patches the module that defines the name. |
| `test_e2e_simulator.py` + `simulator.py` | End to end against a **fake CumulusMX** that parses bodies like the real one (`json=` prefix, `UrlDecode`, regional number parsing, date checks). Add a route there when you add a write endpoint. |
| `test_integration_runtime.py` | Starts the real server as a process (HTTP and stdio) and talks to it with an MCP client: token scopes with real headers, audit file, no PyPI call. |
| `test_architecture.py`, `test_packaging.py`, `test_versioning.py` | Layers, packaging coherence, version / CHANGELOG / skill consistency. |

`tests/compat.py` hides the difference between FastMCP 3.x and 4.x (`readOnlyHint` vs `read_only_hint`). The CI matrix runs both.

New code should import the real modules (`from cumulusmx_mcp import client, safety`) and patch them directly; `import server`
exists so the older tests and external scripts keep working.

## Releases

See the README ("Versioning and releases"). The version lives only in `cumulusmx_mcp/__init__.py`.
