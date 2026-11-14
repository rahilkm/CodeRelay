# CodeRelay — CodeRelay Context

## Project Overview
CodeRelay is a local Windows application that orchestrates multiple AI coding agents (OpenAI Codex CLI as primary, Google Antigravity CLI as fallback) on a shared local project workspace. It independently monitors Codex account rate limits and usage via the Codex App Server JSON-RPC interface, evaluates usage thresholds (90% recommendation, 95% critical), requests explicit user approval before transferring control, and coordinates safe task handoff between agents.

## Repository Information
- Repository URL: https://github.com/rahilkm/CodeRelay.git
- Default Branch: main

## Tech Stack and Frameworks
- Python 3.14+ (standard library emphasized: subprocess, dataclasses, json, pathlib, threading, logging)
- Windows OS (native process management and CMD/PowerShell execution where necessary)
- Typer >= 0.12.0 (CLI framework)
- Codex CLI / Codex App Server (primary coding agent and account rate-limit monitoring via JSON-RPC 2.0)
- Antigravity CLI (agy) (secondary/fallback coding agent)
- Git CLI (local Git operations, status inspection, fast-forward synchronization, credential-manager integration)
- pytest >= 8.0.0 (development and automated testing)

## Project Architecture
CodeRelay is organized into four modular subsystems:
1. Codex Usage Monitoring
   - coderelay.process: Codex CLI executable discovery and Windows process-management helpers.
   - coderelay.rpc: JSON-RPC 2.0 client over standard I/O pipes, supporting request-response matching, asynchronous notification handling, and timeouts.
   - coderelay.usage_parser and coderelay.usage_models: Parsing of account/read and account/rateLimits/read responses into structured usage snapshots and rate-limit windows.
   - coderelay.threshold_detector: State tracking for 90% and 95% thresholds, duplicate-notification suppression, and threshold-state reset when usage drops.
   - coderelay.monitor: Persistent UsageMonitor service polling usage on a configurable schedule (default 120 seconds), independently of the active coding agent.
2. Agent Integration & Workspace Ownership
   - coderelay.agents.base: Unified BaseAgent abstraction defining agent identification, availability detection, process launch, I/O capture, and termination.
   - coderelay.agents.codex: CodexAgent adapter for launching Codex CLI with initial instructions.
   - coderelay.agents.antigravity: AntigravityAgent adapter for launching Antigravity CLI (agy) in the workspace.
   - coderelay.agents.workspace: Thread-safe WorkspaceManager enforcing exclusive single-agent workspace ownership and stale-PID handling.
   - coderelay.agents.manager: AgentManager coordinating agent registration, workspace acquisition, and lifecycle transitions.
3. Project & Git Management
   - coderelay.projects: Local configuration-based ProjectRegistry for registering existing local repositories or cloning from GitHub via Git CLI, without database dependencies.
   - coderelay.context: CODERELAY_CONTEXT.md generation, section-level parsing/updating, and validation.
   - coderelay.git: Git CLI abstraction for repository status, branch tracking, uncommitted-change inspection, conflict detection, and secret-file exclusion.
   - coderelay.sync: GitSyncService providing safe, fast-forward-only repository synchronization with remote tracking branches.
4. Orchestration Engine & Handoff Pipeline (Phases 5A, 5B, 5C)
   - coderelay.orchestrator.orchestrator: Central Orchestrator coordinating startup, initial usage polling, threshold evaluation, user approval, agent-transition decisions, and incoming handoff execution.
   - coderelay.orchestrator.state: In-memory OrchestrationContext tracking active agent, current usage percentage, threshold notifications, and user decisions without persistent execution-history files.
   - coderelay.orchestrator.approval: ApprovalHandler presenting switch recommendations and capturing explicit user confirmation.
   - coderelay.orchestrator.handoff: HandoffService implementing Phase 5B outgoing agent handoff (task completion verification, context check, test runner, isolated selective staging, Git commit/push, remote verification, process termination) and Phase 5C incoming agent handoff (workspace vacancy verification, remote Git fast-forward synchronization, clean working-tree check, Antigravity launch with continuation instructions, and exclusive workspace ownership confirmation).

## Important Directories and Files
- coderelay/ — Main Python package containing CLI, configuration, monitoring, agents, Git integration, context management, and orchestration.
- coderelay.example.json — Example configuration template defining workspace path, agent parameters, and monitoring thresholds.
- codex_usage_test.py — Proof-of-concept script used to verify Codex CLI discovery and App Server JSON-RPC usage retrieval.
- pyproject.toml — Project build configuration, metadata, and dependencies.
- README.md — Project overview and documentation.
- requirements.txt — Project runtime and development dependencies.
- tests/ — Automated tests covering implemented modules.
Local configuration such as coderelay.json, credentials, and tokens must not be included in committed documentation or handoff context.

## Completed Tasks
- Phase 1: Project Setup & Core CLI Foundations — Established project structure, typed configuration loading and validation (coderelay/config.py), base exception hierarchy (coderelay/exceptions.py), and CLI status commands (coderelay/cli.py).
- Phase 2: Persistent Codex Usage Monitor — Implemented Codex CLI discovery, Windows process-management helpers, JSON-RPC 2.0 communication over pipes, usage and rate-limit parsing, threshold detection (90% recommendation and 95% critical) with duplicate suppression and usage-drop resets, and the persistent UsageMonitor service (coderelay monitor). The monitor was tested against the installed Codex App Server.
- Phase 3: Agent Integration Layer — Implemented the common BaseAgent abstraction, CodexAgent, AntigravityAgent, thread-safe in-memory WorkspaceManager for exclusive workspace ownership, and AgentManager lifecycle coordination.
- Phase 4: Project Context & Git Integration — Implemented ProjectRegistry for existing repositories and GitHub clones, CODERELAY_CONTEXT.md generation and section-level operations, safe Git CLI operations with secret-file protection, and GitSyncService for fast-forward-only synchronization.
- Phase 5A: Central Orchestration Engine — Implemented the Orchestrator, startup usage evaluation before agent launch, 90%/95% switching recommendations with duplicate suppression, manual switching workflow (coderelay switch), CLI-based explicit user approval (ApprovalHandler), and the HandoffInterface / HandoffRequest boundary.
- Phase 5B: Outgoing Agent Handoff Execution — Implemented HandoffService for verified task completion, CODERELAY_CONTEXT.md validation, automated test verification, selective isolated Git staging without blind staging of user modifications, commit and push to remote, remote verification, and confirmed process termination before releasing workspace ownership.
- Phase 5C: Incoming Agent Handoff & Workspace Ownership Transfer — Implemented incoming handoff in HandoffService and Orchestrator in the same local directory: outgoing termination verification, remote Git fast-forward synchronization, clean working-tree check, Antigravity CLI launch with structured continuation instructions, exclusive workspace ownership acquisition, and prevention of simultaneous agent execution.

## Current Task and Progress
- Phases 1 through 5C and Phase 6 CLI switch integration are implemented and verified.
- `coderelay switch` is wired to execute the complete Phase 5B + 5C handoff workflow end-to-end with cross-process PID recovery, explicit task completion check, context validation, automated testing, Git staging/commit/push/verify, process termination, safe fast-forward synchronization, and Antigravity launch.
- The reported automated test baseline is 155 passing tests across 15 test modules.

## Pending Tasks
- Phase 6: End-to-End Integration & Reliability
  - Conduct live multi-agent verification in production environments with active Codex CLI and Antigravity CLI sessions.
  - Validate failure handling and process cleanup under live system load.

## Technical Decisions
- Minimal External Dependencies — Use the Python standard library wherever practical. External dependencies are limited to Typer for the CLI and pytest for development/testing.
- Windows Process Management — Use Windows-compatible executable discovery and process-management mechanisms where required. Process-tree cleanup behavior still requires end-to-end verification.
- Persistent App Server Reuse — Maintain one persistent Codex App Server JSON-RPC connection across polling cycles rather than spawning a new process for every poll.
- In-Memory Orchestration State — Do not add a database or persistent execution-history store in V1. Keep runtime orchestration state in memory.
- Exclusive Workspace Ownership — WorkspaceManager ensures that only one AI coding agent owns a workspace at a time, using thread-safe locking and disk lockfile.
- Explicit User Approval — Never switch agents automatically. Require explicit user consent; suppress repeated recommendations within the same threshold band.
- Safe Git Operations — Use Git CLI rather than the GitHub REST API. Use fast-forward-only synchronization, inspect changes before staging, exclude secrets and local configuration, and never use destructive reset or forced-push operations.
- Shared Workspace — Both agents use the same local project directory. GitHub is the remote synchronization point for handoff; do not maintain separate agent clones.

## Known Issues
- Interactive Codex CLI IPC — Writing to subprocess stdin does not provide interactive autonomous task completion for the installed Codex CLI; task completion requires a supported completion handler or explicit user mediation.
- Windows process-tree termination and cleanup have not yet been independently verified end to end in a live environment; do not assume that no orphaned child processes can occur.
- Live end-to-end multi-agent switching across live rate limits remains to be evaluated under Phase 6.

## Testing Instructions and Results
- Run the complete test suite: pytest -v
- Run the CLI switch tests: pytest tests/test_cli_switch.py -v
- Run the handoff tests: pytest tests/test_handoff.py -v
- Run the orchestration tests: pytest tests/test_orchestrator.py -v
- Latest reported test result: 155 passed across 15 test modules. Re-run the suite after changes and record the actual result rather than assuming this baseline still passes.

## Next Agent Instructions
- Read this context file before making changes.
- Verify the current Git branch, working-tree status, and remote configuration before modifying files.
- Work only on the phase and scope explicitly requested.
- Preserve the shared-workspace model and require explicit user approval for every agent switch.
- Never use destructive Git commands, force pushes, or commands that discard uncommitted user changes.
- Inspect staged changes before committing; do not blindly stage the entire repository.
- Never include credentials, tokens, or local configuration such as coderelay.json in commits or context documentation.
- Never modify or delete codex_usage_test.py unless explicitly instructed.
- Run pytest -v after code changes and report the actual test results.
- Update this context file at meaningful implementation milestones and validate it with coderelay context validate.
