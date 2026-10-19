"""Agent base classes for the Tender Co-Pilot pipeline.

Every agent's ``run(ctx)`` must return::

    {"agent": name, "status": "ok" | "failed", "summary": str, "data": dict}

All ``app.*`` imports are deferred to function bodies (TYPE_CHECKING only
here) so agent modules import cleanly even when config/db/ingestion/rules
are still being built by the parallel workstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover
    from app.rag.vectorstore import TenderVectorStore
    from .llm import LLMClient


@dataclass
class AgentContext:
    """Everything an agent may need for one run."""

    db: Any = None                      # SQLAlchemy session
    profile: Optional[dict] = None      # company profile as plain dict
    tender: Optional[dict] = None       # tender row as plain dict
    store: Optional["TenderVectorStore"] = None
    llm: Optional["LLMClient"] = None
    run_id: Optional[int] = None        # PipelineRun id for persisted verdicts
    log: Callable[..., None] = field(
        default=lambda *a, **k: None)   # progress logger, defaults to no-op


class Agent:
    """Base class: name + the standard result envelope."""

    name: str = "agent"

    def run(self, ctx: AgentContext) -> dict:
        raise NotImplementedError

    # -- result helpers ----------------------------------------------------
    def ok(self, summary: str, data: Optional[dict] = None) -> dict:
        return {"agent": self.name, "status": "ok",
                "summary": summary, "data": data or {}}

    def failed(self, summary: str, data: Optional[dict] = None) -> dict:
        return {"agent": self.name, "status": "failed",
                "summary": summary, "data": data or {}}
