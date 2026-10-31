"""Per-request atomic state, indexed reads, bounded async I/O and canonical join."""
import asyncio
from copy import deepcopy

from .diagnostic_runtime import DiagnosticSession, read_receipt
from .adapters import SnapshotSource
from .model import canonical, digest, key


class AtomicContext:
    def __init__(self, tree, graph, pack, intent, snapshot, *, source=None, parallel_reads=4, concurrent=True):
        if type(parallel_reads) is not int or not 1 <= parallel_reads <= 32:
            raise ValueError("parallel read bound must be 1..32")
        # Graph bodies and snapshot belong to this invocation; returned mutable
        # receipts/assessments cannot poison the resident program cache.
        self.tree, self.graph, self.snapshot = deepcopy(tree), deepcopy(graph), deepcopy(snapshot)
        self.pack, self.intent = pack, deepcopy(intent)
        self.snapshot_hash = digest("snapshot", self.snapshot)
        self.blocks = {b["id"]: b for b in self.graph["blocks"]}
        self.index = {}
        for record in self.snapshot["facts"]:
            self.index.setdefault(key(record["atom"]), []).append(record)
        self.allowed_reads = {q for b in self.graph["blocks"] for q in b["read_set"]}
        self.source = source or SnapshotSource(self.snapshot)
        self.concurrent, self.parallel_reads = concurrent, parallel_reads
        self.semaphore = asyncio.Semaphore(parallel_reads)
        self.fetched, self.pending, self.calls = {}, {}, 0

    def acquire_sync(self, identity):
        block = self.blocks[identity]
        return {q: self.index.get(q, []) for q in block["read_set"]}

    async def fetch(self, query):
        if query not in self.allowed_reads:
            raise ValueError("undeclared observation read")
        async with self.semaphore:
            self.calls += 1
            if self.calls > len(self.allowed_reads):
                raise ValueError("observation request budget exceeded")
            envelope = await self.source.read(query, self.snapshot_hash)
            expected = self.index.get(query, [])
            if (envelope["snapshot_hash"] != self.snapshot_hash
                    or canonical(envelope["records"]) != canonical(expected)):
                raise ValueError("observation source returned unpinned records")
            self.fetched[query] = envelope["records"]
            return envelope["records"]

    async def read_once(self, query):
        if query not in self.pending:
            self.pending[query] = asyncio.create_task(self.fetch(query))
        # A cancelled cause must not cancel a shared read required by siblings.
        return await asyncio.shield(self.pending[query])

    async def acquire_async(self, identity):
        queries = self.blocks[identity]["read_set"]
        if self.concurrent:
            values = await asyncio.gather(*(self.read_once(q) for q in queries))
        else:
            values = [await self.read_once(q) for q in queries]
        return dict(zip(queries, values))

    def evaluate(self, identity, records):
        block = self.blocks[identity]
        if set(records) != set(block["read_set"]):
            raise ValueError("atomic block has missing or extra inputs")

        def reader(_snapshot, atom):
            query = key(atom)
            if query not in records:
                raise ValueError("atomic capsule attempted an undeclared read")
            return read_receipt({"as_of": self.snapshot["as_of"], "facts": records[query]}, atom)

        local = DiagnosticSession({**self.tree, "body": block["body"]},
                                  self.pack, self.intent, self.snapshot, receipt_reader=reader)
        for node in block["body"]:
            # Only statically checked read/evaluate nodes occur inside blocks.
            if node["kind"] == "read_symptom":
                local.read_symptom(node["argument"])
            elif node["kind"] == "read_evidence":
                local.read_evidence(node["argument"])
            elif node["kind"] == "evaluate_cause":
                local.evaluate_cause(node["argument"])
            else:
                raise ValueError("unregistered atomic block operation")
        return {"block": identity, "symptom": local.symptom, "evidence": local.evidence,
                "causes": local.causes, "trace": local.trace}

    async def schedule(self, functions):
        if not self.concurrent:
            return [await function(self) for function in functions]
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(function(self)) for function in functions]
        return [task.result() for task in tasks]

    async def close(self):
        for task in self.pending.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.pending.values(), return_exceptions=True)

    def finish(self, outputs):
        expected = self.graph["join"]["depends_on"]
        if [o["block"] for o in outputs] != expected:
            raise ValueError("atomic join has incomplete or noncanonical inputs")
        session = DiagnosticSession(self.tree, self.pack, self.intent, self.snapshot)
        session.symptom = outputs[0]["symptom"]
        for output in outputs:
            if set(output["evidence"]) & set(session.evidence):
                raise ValueError("atomic blocks wrote conflicting evidence outputs")
            session.evidence.update(output["evidence"])
            session.causes.extend(output["causes"])
            session.trace.extend(output["trace"])
        session.pc = len(self.tree["body"]) - 2
        session.rank_causes(0)
        return session.complete_diagnosis("DIAGNOSIS_EVALUATED")
