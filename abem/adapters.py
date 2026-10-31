"""Explicit implementation bindings and read-only access to a pinned snapshot."""
from copy import deepcopy

from .model import digest, key


def _valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class AdapterRegistry:
    """Caller-supplied callbacks paired with declared implementation hashes.

    Registry creation is a trusted host operation. Domain data never imports or
    discovers executable Python implementations. Hashes establish identity;
    callbacks still require host trust and are not OS-sandboxed.
    """
    def __init__(self, implementations=None):
        self._implementations = dict(implementations or {})
        for name, (callback, implementation_hash) in self._implementations.items():
            if not isinstance(name, str) or not name or not callable(callback) or not _valid_hash(implementation_hash):
                raise ValueError("invalid explicit adapter implementation")

    def pins(self):
        return {name: value[1] for name, value in sorted(self._implementations.items())}

    def verify(self, bindings):
        actual = self.pins()
        if any(actual.get(name) != pin for name, pin in bindings.items()):
            raise ValueError("adapter implementation is missing or its hash changed")

    def invoke(self, op, bindings, state, declared):
        name = op["adapter"]
        if name not in declared:
            raise ValueError("undeclared host adapter")
        self.verify({name: declared[name]})
        return self._implementations[name][0](deepcopy(op), deepcopy(bindings), state)


def validate_bindings(pack, adapters=None):
    declared = pack.get("adapter_bindings", {})
    if not isinstance(declared, dict) or any(not isinstance(k, str) or not _valid_hash(v) for k, v in declared.items()):
        raise ValueError("invalid adapter implementation bindings")
    if any(op["adapter"] not in declared for op in pack["operators"]):
        raise ValueError("undeclared host adapter")
    if adapters is not None:
        if not isinstance(adapters, AdapterRegistry):
            raise ValueError("execution requires an explicit AdapterRegistry")
        adapters.verify(declared)
    return declared


class SnapshotSource:
    """Generic async reader for existing records; no simulated delays/failures."""
    def __init__(self, snapshot):
        self.snapshot_hash = digest("snapshot", snapshot)
        self.rows = {}
        for record in snapshot["facts"]:
            self.rows.setdefault(key(record["atom"]), []).append(deepcopy(record))
        self.calls, self.active, self.max_active = 0, 0, 0

    async def read(self, query, snapshot_hash):
        if snapshot_hash != self.snapshot_hash:
            raise ValueError("observation source snapshot mismatch")
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            return {"snapshot_hash": self.snapshot_hash, "records": deepcopy(self.rows.get(query, []))}
        finally:
            self.active -= 1
