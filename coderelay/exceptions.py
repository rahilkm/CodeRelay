"""Custom exception hierarchy for CodeRelay."""


class CodeRelayError(Exception):
    """Base exception for all CodeRelay errors."""


class ConfigError(CodeRelayError):
    """Base exception for configuration-related errors."""


class ConfigNotFoundError(ConfigError):
    """Raised when a configuration file cannot be found."""


class ConfigFormatError(ConfigError):
    """Raised when configuration content is not valid JSON or syntax is corrupted."""


class ConfigValidationError(ConfigError):
    """Raised when configuration values fail validation rules."""


class CodexError(CodeRelayError):
    """Base exception for Codex integration errors."""


class CodexNotFoundError(CodexError):
    """Raised when Codex CLI binary or command cannot be located."""


class CodexProcessError(CodexError):
    """Raised when Codex App Server process fails to start or terminates unexpectedly."""


class JsonRpcError(CodeRelayError):
    """Base exception for JSON-RPC communication errors."""


class JsonRpcTimeoutError(JsonRpcError):
    """Raised when a JSON-RPC request times out waiting for a response."""


class JsonRpcConnectionClosedError(JsonRpcError):
    """Raised when the JSON-RPC pipe/connection is closed while waiting for a response."""


class AgentError(CodeRelayError):
    """Base exception for agent-related errors."""


class AgentNotFoundError(AgentError):
    """Raised when a requested agent adapter is unknown or not configured."""


class AgentNotAvailableError(AgentError):
    """Raised when an agent's CLI executable is not installed or not in PATH."""


class AgentStartError(AgentError):
    """Raised when an agent process fails to start."""


class WorkspaceOccupiedError(CodeRelayError):
    """Raised when attempting to launch an agent in a workspace already owned by another active agent."""


class GitError(CodeRelayError):
    """Base exception for Git operations."""


class GitNotInstalledError(GitError):
    """Raised when Git CLI is not installed or not found in PATH."""


class NotAGitRepositoryError(GitError):
    """Raised when a directory is not a valid Git repository."""


class GitSyncError(GitError):
    """Raised when a Git synchronization operation fails."""


class UnsafeSyncError(GitSyncError):
    """Raised when synchronization cannot safely proceed due to uncommitted changes or divergent branches."""


class MergeConflictError(GitSyncError):
    """Raised when merge conflicts are detected."""


class ProjectError(CodeRelayError):
    """Base exception for project registry operations."""


class ProjectAlreadyExistsError(ProjectError):
    """Raised when attempting to register a project with an already registered name or path."""


class ProjectNotFoundError(ProjectError):
    """Raised when a specified project cannot be found in the registry."""


class ContextError(CodeRelayError):
    """Base exception for project context operations."""


class ContextValidationError(ContextError):
    """Raised when CODERELAY_CONTEXT.md fails schema or required section validation."""


class OrchestratorError(CodeRelayError):
    """Base exception for orchestration engine errors."""


class NoActiveAgentError(OrchestratorError):
    """Raised when an operation requires an active agent but none is running."""


class HandoffError(OrchestratorError):
    """Base exception for handoff workflow errors."""


class HandoffTaskCompletionError(HandoffError):
    """Raised when the outgoing agent fails to finish its current operation."""


class HandoffContextError(HandoffError):
    """Raised when CODERELAY_CONTEXT.md is missing or invalid during handoff."""


class HandoffTestFailureError(HandoffError):
    """Raised when required tests fail during handoff verification."""


class HandoffGitStateError(HandoffError):
    """Raised when Git working tree is in an unsafe state (conflicts, protected files)."""


class HandoffCommitError(HandoffError):
    """Raised when creating the handoff commit fails."""


class HandoffPushError(HandoffError):
    """Raised when pushing the handoff commit to remote fails."""


class HandoffVerificationError(HandoffError):
    """Raised when remote verification of commit or context file fails."""


class HandoffProcessShutdownError(HandoffError):
    """Raised when the outgoing agent process fails to stop cleanly."""




