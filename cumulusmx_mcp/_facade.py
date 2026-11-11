"""Fachada de compatibilidad: `import server` (módulo único) sigue funcionando sobre el paquete modular.

La fachada es un módulo cuyos atributos se resuelven en el módulo del paquete que los define; las asignaciones (p. ej.
`patch.object(server, "_get", ...)`) se aplican en ese módulo. Así el código antiguo y los tests no necesitan saber en
qué fichero vive cada cosa.
"""
import importlib
import sys
import types

# Orden de búsqueda: primero los módulos que DEFINEN las cosas (los que solo las importan van después).
_MODULES = (
    "config", "utils", "safety", "backups", "client", "capture", "app", "dayrows", "analysis", "settings_engine", "alerts", "reporting",
    "tools.observations", "tools.graphs", "tools.records", "tools.climate", "tools.reports", "tools.maintenance",
    "tools.diary", "tools.settings", "tools.editors", "tools.alerts", "tools.period_report", "tools.safety_tools",
    "resources", "prompts", "cli",
)
_PKG = __package__


class _Facade(types.ModuleType):
    def __init__(self, name: str):
        super().__init__(name, "Fachada de compatibilidad sobre el paquete cumulusmx_mcp")
        object.__setattr__(self, "_mods", None)
        object.__setattr__(self, "_owners", {})

    def _modules(self):
        mods = object.__getattribute__(self, "_mods")
        if mods is None:
            importlib.import_module(f"{_PKG}.tools")
            mods = []
            for m in _MODULES:
                try:
                    mods.append(importlib.import_module(f"{_PKG}.{m}"))
                except ModuleNotFoundError as exc:  # módulos opcionales aún no creados
                    if exc.name != f"{_PKG}.{m}":
                        raise
            object.__setattr__(self, "_mods", mods)
        return mods

    def _owner(self, name: str):
        # La caché de propietarios sobrevive a `delattr`: `patch.object` restaura un atributo borrándolo y reasignándolo,
        # y la reasignación debe volver al mismo módulo.
        owners = object.__getattribute__(self, "_owners")
        if name in owners:
            return owners[name]
        for mod in self._modules():
            if name in vars(mod):
                owners[name] = mod
                return mod
        return None

    def __getattr__(self, name: str):
        if name.startswith("__") and name.endswith("__"):
            if name == "__version__":
                return importlib.import_module(_PKG).__version__
            raise AttributeError(name)
        mod = self._owner(name)
        if mod is None:
            raise AttributeError(f"module 'server' has no attribute {name!r}")
        return getattr(mod, name)

    def __setattr__(self, name: str, value):
        mod = None if name.startswith("__") else self._owner(name)
        if mod is None:
            super().__setattr__(name, value)
        else:
            setattr(mod, name, value)

    def __delattr__(self, name: str):
        mod = None if name.startswith("__") else self._owner(name)
        if mod is None:
            super().__delattr__(name)
        else:
            delattr(mod, name)

    def __dir__(self):
        names = set()
        for mod in self._modules():
            names.update(n for n in vars(mod) if not n.startswith("__"))
        return sorted(names)


def install(name: str = "server") -> types.ModuleType:
    """Registra (una sola vez) la fachada como `sys.modules[name]` y la devuelve."""
    existing = sys.modules.get(name)
    if isinstance(existing, _Facade):
        return existing
    facade = _Facade(name)
    sys.modules[name] = facade
    return facade
