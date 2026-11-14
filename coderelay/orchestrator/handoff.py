"""Handoff interface boundary connecting orchestration to the handoff pipeline."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HandoffRequest:
    """Represents an approved request to hand off a workspace between agents."""

    from_agent: str
    to_agent: str
    workspace_path: Path
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc).astimezone())
    status: str = "initiated"


class HandoffInterface(ABC):
    """Abstract interface defining the handoff boundary."""

    @abstractmethod
    def initiate_handoff(self, request: HandoffRequest) -> HandoffRequest:
        """Initiate the handoff workflow."""
        ...


class Phase5AHandoffInitiator(HandoffInterface):
    """Phase 5A handoff initiator implementation.

    Records the approved switch and prepares the handoff boundary for full execution in 5B/5C.
    """

    def __init__(self) -> None:
        self.last_request: HandoffRequest | None = None

    def initiate_handoff(self, request: HandoffRequest) -> HandoffRequest:
        logger.info(
            "Handoff workflow initiated: '%s' -> '%s' in %s (reason: %s)",
            request.from_agent,
            request.to_agent,
            request.workspace_path,
            request.reason,
        )
        self.last_request = request
        request.status = "initiated"
        return request
