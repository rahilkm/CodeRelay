"""Agent registry for managing available coding agent adapters."""

from __future__ import annotations

from typing import Optional

from coderelay.agents.antigravity import AntigravityAgent
from coderelay.agents.base import BaseAgent
from coderelay.agents.codex import CodexAgent
from coderelay.config import Config
from coderelay.exceptions import AgentNotFoundError


class AgentRegistry:
    """Registry holding known agent adapters."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """Register an agent adapter."""
        self._agents[agent.name.lower()] = agent

    def get(self, name: str) -> BaseAgent:
        """Get an agent by name. Raises AgentNotFoundError if missing."""
        key = name.strip().lower()
        # Support common aliases
        if key == "agy":
            key = "antigravity"

        agent = self._agents.get(key)
        if agent is None:
            available = ", ".join(self._agents.keys())
            raise AgentNotFoundError(f"Agent '{name}' is unknown. Available agents: {available}")
        return agent

    def list_all(self) -> list[BaseAgent]:
        """Return a list of all registered agents."""
        return list(self._agents.values())

    def is_available(self, name: str) -> bool:
        """Check if the named agent is available on this system."""
        try:
            return self.get(name).is_available()
        except AgentNotFoundError:
            return False

    @classmethod
    def from_config(cls, config: Optional[Config] = None) -> AgentRegistry:
        """Create an AgentRegistry populated from configuration or defaults."""
        registry = cls()

        codex_cmd = (
            config.primary_agent.command
            if config and config.primary_agent and config.primary_agent.command
            else "codex"
        )
        agy_cmd = (
            config.secondary_agent.command
            if config and config.secondary_agent and config.secondary_agent.command
            else "agy"
        )

        registry.register(CodexAgent(command=codex_cmd))
        registry.register(AntigravityAgent(command=agy_cmd))
        return registry
