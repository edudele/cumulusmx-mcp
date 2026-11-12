# Contributing

1. `pip install -e ".[dev]"`
2. Make the change. Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first: the layer rules are enforced by tests.
3. `ruff check .` and `python -m pytest -q` must pass. If you touch a write endpoint, add or extend its route in
   `tests/simulator.py` and an end-to-end test in `tests/test_e2e_simulator.py` (a mock cannot catch a wrong body format).
4. New or changed tool: update `skills/cumulusmx-weather/reference.md`, the README tables and `CHANGELOG.md`
   (add the entry under the version you are preparing and bump `__version__` and the skill's `metadata.version`).
5. Endpoint names, parameters and request formats come from the CumulusMX source (`Api.cs`, `DataEditor.cs`, `Settings/*.cs`),
   not from guesses; the wiki is sometimes out of date. Say in the PR which file you checked.
6. Keep tool descriptions precise: they are what the model reads.

## Publishing to PyPI (one-time setup)

The `Publish to PyPI` workflow uses PyPI *trusted publishing* (OIDC): no API token is stored anywhere. It always builds and
checks the package for a release tag, but only uploads when the repository variable `PYPI_PUBLISH` is `true`.

1. On pypi.org -> *Your account* -> *Publishing* -> *Add a new pending publisher*: project `cumulusmx-mcp`, owner `edudele`,
   repository `cumulusmx-mcp`, workflow `pypi-publish.yml`, environment `pypi`.
2. On GitHub -> *Settings* -> *Environments*: create the environment `pypi` (optionally with a required reviewer to approve each upload).
3. On GitHub -> *Settings* -> *Secrets and variables* -> *Actions* -> *Variables*: add `PYPI_PUBLISH` = `true`.
4. The next release (`Release` workflow, or pushing the `vX.Y.Z` tag) uploads the sdist and wheel. To publish a version that
   already has its tag, run *Actions -> Publish to PyPI -> Run workflow* choosing that tag as the ref.

## Testing against a real CumulusMX

`pytest` (the fast suite) uses a strict fake CumulusMX (`tests/simulator.py`). The `Integration (real CumulusMX)` workflow
additionally starts the **official CumulusMX release** with its Simulator station and runs `tests_integration/` against it:
every tool is called, every edit is undone with `restore_backup`, and the real data files are compared byte by byte.
It runs on every push to a branch (not `main`) and takes about 5 minutes.

To run it by hand, start a CumulusMX with `[Station] Type=17` (Simulator), seed it with `python -m tests_integration.seed <data dir>`
and run:

```bash
CUMULUSMX_REAL_URL=http://127.0.0.1:8998 CUMULUSMX_DATA_DIR=/path/to/CumulusMX/data python -m pytest tests_integration -v
```

**Release gate.** The `Release` workflow refuses to publish unless both `tests.yml` and `integration.yml` have a green run for the
exact commit being released, so push the commit to a branch and wait for CI before running it. No manual approval is involved.

## Testing writes against a copy of your own station

Never point a test MCP at the production CumulusMX. Run a second CumulusMX with a *copy* of your data and, in that copy, disable
every upload (FTP, MQTT, MySQL, third-party services, external programs) or point MySQL at a separate database. Give the MCP
container a data volume its user can write to, otherwise edits are refused because their backup cannot be saved.
Step by step, with a compose example: [`deploy/sandbox/README.md`](deploy/sandbox/README.md) (in Spanish).
