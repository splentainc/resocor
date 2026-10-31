"""Capability-checked opcode interpreter with explicit adapters and replay."""
from copy import deepcopy

from .compiler import load
from .model import (bind, closure, decision_result, digest, key, observations,
                    truth, validate_atom)


class Session:
    """Explicit invocation state shared by bytecode and generated Python hosts."""

    def __init__(self, binary, manifest, pack, intent, snapshot, world, replay=None, permissions=None, adapters=None):
        payload = load(binary, manifest, pack, intent, snapshot, adapters)
        self.adapters = adapters
        self.plan = payload["plan"]
        self.code = payload["code"]
        self.pack, self.intent, self.world = pack, intent, world
        self.current, self.replay = deepcopy(snapshot), replay
        self.trace, self.invoked, self.last, self.pc = [], [], None, 0
        self.scope = set(intent["permissions"] if permissions is None else permissions)
        self.scope &= set(intent["permissions"])

    def finish(self, status, reason):
        return {"status": status, "reason": reason, "snapshot": self.current, "trace": self.trace,
                "invoked": self.invoked, "assurance": ["SIMULATED_SUCCESS"]
                if status == "GOAL_CONFIRMED" else [], "real_world_success": False,
                "causal_scope": "consistency with encoded fictional model; no independent model validation"}

    def state_now(self):
        base, conflicts, _ = observations(self.current)
        if conflicts:
            raise ValueError("runtime evidence conflict")
        return closure(self.pack, base)

    def check(self, node):
        return self.step(1, node)

    def authorize(self, node):
        return self.step(2, node)

    def invoke(self, node):
        return self.step(3, node)

    def expect(self, node):
        return self.step(4, node)

    def assert_goal(self, goal):
        return self.step(5, goal)

    def complete(self, status):
        return self.step(6, status)

    def step(self, code, arg):
        pc = self.pc
        if pc >= 4 * self.intent["search"]["max_depth"] + 2:
            raise ValueError("instruction budget exceeded")
        if pc >= len(self.code) or self.code[pc] != [code, arg]:
            raise ValueError("generated program deviates from verified AST")
        self.pc += 1
        state, derivations = self.state_now()
        if not all(truth(state, a) is True for a in self.intent["invariants"]):
            return self.finish("MODEL_CONFLICT", "runtime safety invariant not established")
        if code == 5:
            if truth(state, arg) is not True:
                return self.finish("REPLAN", "recorded observations do not establish goal")
            self.trace.append({"event": "goal", "goal": arg, "derivations": derivations,
                          "evidence": state[key(arg)]["evidence"], "simulation": True})
            return None
        if code == 6:
            return self.finish(arg, "goal supported by recorded adapter observations under the declared model")
        node = self.plan["nodes"][arg]
        op = next(o for o in self.pack["operators"] if o["id"] == node["op"])
        if code == 1:
            values = [truth(state, a) for a in node["requires"]]
            self.trace.append({"event": "check", "node": arg, "values": values,
                          "derivations": derivations})
            if None in values:
                return self.finish("NEEDS_OBSERVATION", "runtime precondition unknown")
            if False in values:
                return self.finish("REPLAN", "runtime precondition contradicted")
            if op.get("decision"):
                result = decision_result(self.pack, op["decision"], node["bindings"], state)
                self.trace.append({"event": "decision", **result})
                if result["result"] is not True:
                    return self.finish("REQUIRES_AUTHORIZATION", "movement policy did not permit action")
        elif code == 2:
            if node["capability"] not in self.scope:
                self.trace.append({"event": "authorization", "operator": node["op"],
                              "capability": node["capability"], "allowed": False})
                return self.finish("REQUIRES_AUTHORIZATION", f"missing {node['capability']}")
        elif code == 3:
            event_id = f"adapter:{len(self.current['facts'])}:{pc}:{digest('snapshot', self.current)[:12]}"
            if self.replay is None:
                if self.adapters is None:
                    raise ValueError("effectful execution requires an explicit AdapterRegistry")
                self.last = self.adapters.invoke(op, node["bindings"], self.world, self.pack["adapter_bindings"])
            else:
                if not self.replay:
                    raise ValueError("replay missing adapter result")
                record = self.replay.pop(0)
                if (record["operator"] != node["op"] or record["bindings"] != node["bindings"]
                        or record["capsule_hash"] != node["capsule_hash"] or record["id"] != event_id):
                    raise ValueError("replay invocation mismatch")
                self.last = deepcopy(record["result"])
            if self.last["outcome"] not in op["outcomes"]:
                raise ValueError("undeclared adapter outcome")
            if self.last["outcome"] in ("expected", "different"):
                if sorted(key(a) for a in self.last["observations"]) != sorted(key(a) for a in node["expected"]):
                    raise ValueError("adapter output schema mismatch")
                for a in self.last["observations"]:
                    validate_atom(self.pack, a)
                if (self.last["observations"] == node["expected"]) != (self.last["outcome"] == "expected"):
                    raise ValueError("adapter outcome disagrees with observations")
            elif self.last["observations"]:
                raise ValueError("incomplete adapter result includes untrusted observations")
            event = {"id": event_id, "event": "adapter", "operator": node["op"],
                     "bindings": node["bindings"], "capsule_hash": node["capsule_hash"],
                     "outcome": self.last["outcome"], "result": deepcopy(self.last), "simulation": True}
            self.trace.append(event)
            self.invoked.append([node["op"], node["bindings"]])
            if self.last["outcome"] == "failed":
                return self.finish("EXECUTION_FAILED", "adapter failed or completion uncertain; dependent steps cancelled")
            if self.last["outcome"] == "inconclusive":
                return self.finish("NEEDS_OBSERVATION", "adapter observation inconclusive")
            # Only attributed adapter outputs enter the observed snapshot. Search
            # predictions never enter it. Prior observations remain in self.trace/input artifacts.
            remove = {key(a) for a in node["invalidates"] + self.last["observations"]}
            self.current["facts"] = [f for f in self.current["facts"] if key(f["atom"]) not in remove]
            for i, a in enumerate(self.last["observations"]):
                self.current["facts"].append({"id": f"{event_id}:{i}", "atom": a,
                    "source": event_id, "unit": self.pack["predicates"][a[0]]["unit"],
                    "observed_at": self.current["as_of"], "valid_from": self.current["as_of"],
                    "valid_to": self.current["as_of"] + 100})
            event["snapshot_after"] = deepcopy(self.current)
        elif code == 4:
            if self.last["outcome"] != "expected":
                return self.finish("REPLAN", "observation contradicts candidate; invalidate prediction and replan")
        else:
            raise ValueError("undeclared opcode")
        return None


def execute(binary, manifest, pack, intent, snapshot, world, replay=None, permissions=None, adapters=None):
    session = Session(binary, manifest, pack, intent, snapshot, world, replay, permissions, adapters)
    for code, arg in session.code:
        result = session.step(code, arg)
        if result is not None:
            return result
    raise ValueError("missing terminal instruction")
