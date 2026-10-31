"""Request-local, opt-in measurements of actual runtime work (no model traces)."""
from contextlib import contextmanager
from datetime import datetime, timezone
from time import perf_counter_ns


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Trace:
    def __init__(self):
        self.started_ns = perf_counter_ns()
        self.started_at = timestamp()
        self.steps = []
        self.rules = []
        self.receipt = None

    def skipped(self, stage, reason):
        self.steps.append({"stage": stage, "status": "skipped", "reason": reason,
                           "started_at": None, "ended_at": None, "elapsed_us": None,
                           "start_us": None, "end_us": None})

    def finish(self):
        return {"started_at": self.started_at, "ended_at": timestamp(),
                "total_us": (perf_counter_ns() - self.started_ns) / 1000,
                "steps": self.steps, "rules": self.rules}


@contextmanager
def span(trace, stage):
    step = {"stage": stage, "status": "completed"}
    if trace is None:
        yield step
        return
    start = perf_counter_ns()
    step.update(started_at=timestamp(), start_us=(start - trace.started_ns) / 1000)
    trace.steps.append(step)
    try:
        yield step
    except Exception as exc:
        step.update(status="blocked", reason=str(exc))
        raise
    finally:
        end = perf_counter_ns()
        step.update(ended_at=timestamp(), elapsed_us=(end - start) / 1000,
                    end_us=(end - trace.started_ns) / 1000)
