"""Test bootstrap.

The parser tests exercise pure functions, so they must not require a full
Home Assistant install. `asic_api` imports httpx at module level; when httpx is
absent we substitute a stub that satisfies the import and nothing else.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - exercised only on bare interpreters
    import httpx  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    stub = types.ModuleType("httpx")

    class _Missing:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("httpx is not installed")

    stub.AsyncClient = _Missing
    stub.DigestAuth = _Missing
    stub.HTTPError = type("HTTPError", (Exception,), {})
    sys.modules["httpx"] = stub


def load_module(name: str):
    """Import one integration module without executing the package __init__.

    The package's __init__ pulls in Home Assistant; these tests run on a bare
    interpreter. A synthetic parent package is registered so the module's
    relative imports (`from .const import ...`) still resolve.
    """
    pkg_name = "asic_fleet_under_test"
    component = ROOT / "custom_components" / "asic_fleet"

    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(component)]
        sys.modules[pkg_name] = pkg

    full_name = f"{pkg_name}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    spec = importlib.util.spec_from_file_location(full_name, component / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module
