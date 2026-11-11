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
