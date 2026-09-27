"""youwei-worker entrypoint.

Re-exports loop.main for the console script; the handler table lives
there so tests can import the same composition."""

from youwei_core.worker.loop import main

__all__ = ["main"]
