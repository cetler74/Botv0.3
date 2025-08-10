# Re-export from the hyphenated module path
import importlib.util
import sys
from pathlib import Path

# Dynamically load the implementation from the hyphenated directory name
_impl_path = Path(__file__).resolve().parents[1] / "database-service" / "main.py"
_spec = importlib.util.spec_from_file_location("services.database_service._impl", str(_impl_path))
if _spec and _spec.loader:
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["services.database_service._impl"] = _module
    _spec.loader.exec_module(_module)
    # Re-export public attributes
    for _name in dir(_module):
        if not _name.startswith("_"):
            globals()[_name] = getattr(_module, _name)
else:
    raise ImportError(f"Could not load database service implementation from {_impl_path}")


