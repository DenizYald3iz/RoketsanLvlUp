"""Every module in this folder is imported on startup, so its @stage_tool functions register themselves.
Add a new file here (e.g. my_tools.py) — no other wiring needed."""
import importlib
import pkgutil

for _m in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_m.name}")
