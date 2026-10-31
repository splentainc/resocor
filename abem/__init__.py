"""Experimental Atomic Binary Execution Model; standard library, Python 3.12+."""

__version__ = "0.1.0"
EXPERIMENTAL = True
__all__ = ["diagnose", "AtomicRunner", "AdapterRegistry", "SnapshotSource", "EXPERIMENTAL", "__version__"]


def __getattr__(name):
    # Keep package import cheap for generated hosts. Public runtime APIs load
    # lazily; no domain pack, fixture, benchmark or adapter is imported here.
    if name == "diagnose":
        from .diagnosis import diagnose
        value = diagnose
    elif name == "AtomicRunner":
        from .atomic_runner import AtomicRunner
        value = AtomicRunner
    elif name in ("AdapterRegistry", "SnapshotSource"):
        from .adapters import AdapterRegistry, SnapshotSource
        value = {"AdapterRegistry": AdapterRegistry, "SnapshotSource": SnapshotSource}[name]
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
