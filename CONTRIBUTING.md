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
