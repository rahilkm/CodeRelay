"""Command-line interface for CodeRelay."""

from __future__ import annotations

import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from coderelay import __version__
from coderelay.agents.manager import AgentManager
from coderelay.agents.registry import AgentRegistry
from coderelay.config import DEFAULT_CONFIG_FILENAME, load_config
from coderelay.context import (
    CONTEXT_FILENAME,
    init_context,
    read_context,
    update_context_section,
    validate_context,
)
from coderelay.exceptions import (
    AgentError,
    AgentNotAvailableError,
    AgentNotFoundError,
    AgentStartError,
    CodeRelayError,
    ConfigError,
    ContextError,
    GitError,
    GitSyncError,
    MergeConflictError,
    NotAGitRepositoryError,
    ProjectError,
    UnsafeSyncError,
    WorkspaceOccupiedError,
)
from coderelay.git import (
    fetch_remote,
    get_git_status,
    is_git_repo,
)
from coderelay.monitor import UsageMonitor
from coderelay.projects import ProjectRegistry
from coderelay.sync import GitSyncService, SyncStatus
from coderelay.usage_models import ThresholdEvent, ThresholdLevel, UsageSnapshot

app = typer.Typer(
    name="coderelay",
    help="CodeRelay: Orchestrate AI coding agents with user-approved handoff.",
    no_args_is_help=True,
    add_completion=False,
)

agent_app = typer.Typer(
    name="agent",
    help="Manage and inspect coding agents in a workspace.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(agent_app, name="agent")

project_app = typer.Typer(
    name="project",
    help="Register, inspect, and manage CodeRelay projects.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(project_app, name="project")

context_app = typer.Typer(
    name="context",
    help="Manage and validate project context (CODERELAY_CONTEXT.md).",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(context_app, name="context")

git_app = typer.Typer(
    name="git",
    help="Safe Git status and fast-forward-only synchronization.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(git_app, name="git")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"CodeRelay {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """CodeRelay: Local AI coding agent orchestrator."""


def _resolve_workspace(
    workspace_opt: Optional[Path],
    config_opt: Optional[Path] = None,
) -> Path:
    """Helper to resolve target workspace from option, config, or cwd."""
    if workspace_opt is not None:
        return workspace_opt.expanduser().resolve()

    try:
        cfg, _ = load_config(config_opt)
        return cfg.workspace.resolved_path
    except ConfigError:
        return Path.cwd().resolve()


def _get_agent_manager(config_opt: Optional[Path]) -> AgentManager:
    """Build an AgentManager populated with configuration if available."""
    cfg = None
    try:
        cfg, _ = load_config(config_opt)
    except ConfigError:
        pass
    registry = AgentRegistry.from_config(cfg)
    return AgentManager(registry=registry)


# ----------------------------------------------------------------------
# Core / Status / Monitor commands
# ----------------------------------------------------------------------


@app.command(name="status")
def status(
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to coderelay configuration file.",
        exists=False,
    ),
) -> None:
    """Display configuration, agent, and workspace status."""
    try:
        config, resolved_config_path = load_config(config_path)
    except ConfigError as err:
        typer.secho(f"Configuration error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    ws = config.workspace
    ws_exists = ws.resolved_path.exists()
    ws_is_dir = ws.resolved_path.is_dir() if ws_exists else False
    ctx_file = ws.context_file_path
    ctx_exists = ctx_file.is_file() if ws_exists else False

    typer.echo("=== CodeRelay Status ===")
    typer.echo(f"Configuration file : {resolved_config_path}")
    typer.echo("")
    typer.echo("[Workspace]")
    typer.echo(f"  Path             : {ws.resolved_path}")
    typer.echo(f"  Directory exists : {'Yes' if (ws_exists and ws_is_dir) else 'No'}")
    typer.echo(f"  Context file     : {ctx_file.name}")
    typer.echo(f"  Context exists   : {'Yes' if ctx_exists else 'No'}")
    typer.echo("")
    typer.echo("[Agents]")
    typer.echo(
        f"  Primary (Codex)  : name='{config.primary_agent.name}', "
        f"command='{config.primary_agent.command}', enabled={config.primary_agent.enabled}"
    )
    typer.echo(
        f"  Secondary (AGY)  : name='{config.secondary_agent.name}', "
        f"command='{config.secondary_agent.command}', enabled={config.secondary_agent.enabled}"
    )
    typer.echo("")
    typer.echo("[Monitoring]")
    typer.echo(f"  Check interval   : {config.monitoring.interval_seconds}s")
    typer.echo(f"  Recommend handoff: {config.monitoring.recommend_threshold}%")
    typer.echo(f"  Critical handoff : {config.monitoring.critical_threshold}%")


@app.command(name="monitor")
def monitor(
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to coderelay configuration file.",
        exists=False,
    ),
) -> None:
    """Start persistent Codex usage monitor."""
    try:
        config, _ = load_config(config_path)
    except ConfigError as err:
        typer.secho(f"Configuration error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    def print_snapshot(snapshot: UsageSnapshot) -> None:
        ts_str = snapshot.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        typer.echo(f"\n--- Codex Usage [{ts_str}] ---")
        typer.echo(f"Plan Type        : {snapshot.account.plan_type}")

        if not snapshot.buckets:
            typer.echo("Usage Windows    : No rate limit data returned.")
            return

        for bucket_id, bucket in snapshot.buckets.items():
            typer.echo(f"Bucket '{bucket_id}':")
            if not bucket.all_windows:
                typer.echo("  No active usage windows.")
                continue

            for window in bucket.all_windows:
                reset_str = (
                    window.resets_at.strftime("%Y-%m-%d %H:%M:%S")
                    if window.resets_at
                    else "N/A"
                )
                typer.echo(
                    f"  {window.name:<12}: {window.used_percent:5.1f}% used "
                    f"({window.remaining_percent:5.1f}% remaining) | Resets at: {reset_str}"
                )

            if bucket.rate_limit_reached_type:
                typer.secho(
                    f"  Limit Reached: {bucket.rate_limit_reached_type}",
                    fg=typer.colors.YELLOW,
                )

    def print_threshold_event(event: ThresholdEvent) -> None:
        if event.level == ThresholdLevel.CRITICAL:
            typer.echo("")
            typer.secho(
                f"[CRITICAL THRESHOLD] {event.message}",
                fg=typer.colors.RED,
                bold=True,
            )
        else:
            typer.echo("")
            typer.secho(
                f"[RECOMMENDATION] {event.message}",
                fg=typer.colors.YELLOW,
                bold=True,
            )

    def print_status_msg(msg: str) -> None:
        typer.echo(f"[Monitor] {msg}")

    monitor_svc = UsageMonitor(
        config=config,
        on_snapshot=print_snapshot,
        on_threshold_event=print_threshold_event,
        on_status_message=print_status_msg,
    )

    def sigint_handler(signum: int, frame: object) -> None:
        typer.echo("\nInterrupt received. Stopping monitor cleanly...")
        monitor_svc.stop()

    signal.signal(signal.SIGINT, sigint_handler)
    signal.signal(signal.SIGTERM, sigint_handler)

    try:
        monitor_svc.run()
    except KeyboardInterrupt:
        monitor_svc.stop()
    except Exception as e:
        typer.secho(f"Monitor error: {e}", fg=typer.colors.RED, err=True)
        monitor_svc.stop()
        raise typer.Exit(code=1)


# ----------------------------------------------------------------------
# Agent Commands
# ----------------------------------------------------------------------


@app.command(name="agents")
@agent_app.command(name="list")
def list_agents(
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to coderelay configuration file.",
    ),
) -> None:
    """List available coding agents and their installation status."""
    manager = _get_agent_manager(config_path)
    typer.echo("=== Configured Agents ===")
    for a in manager.registry.list_all():
        avail = a.is_available()
        status_color = typer.colors.GREEN if avail else typer.colors.RED
        status_text = "Available" if avail else "Not found in PATH"
        typer.echo(
            f"- {a.name:<12} ({a.display_name:<16}): "
            + typer.style(f"[{status_text}]", fg=status_color)
            + f" (command: '{a.command}', path: {a.get_executable_path() or 'N/A'})"
        )


@agent_app.command(name="start")
def agent_start(
    agent_name: str = typer.Argument(..., help="Name of the agent to launch (e.g. 'codex', 'antigravity')."),
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Target project workspace directory. Defaults to configured workspace or current directory.",
    ),
    instruction: Optional[str] = typer.Option(
        None,
        "--instruction",
        "-i",
        help="Optional initial instruction or prompt for the agent.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to coderelay configuration file.",
    ),
) -> None:
    """Launch a selected agent in the specified workspace."""
    ws = _resolve_workspace(workspace, config_path)
    manager = _get_agent_manager(config_path)

    try:
        agent = manager.start_agent(
            agent_name=agent_name,
            workspace=ws,
            instruction=instruction,
        )
        typer.secho(f"Agent '{agent.name}' started successfully.", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"  PID        : {agent.get_pid()}")
        typer.echo(f"  Workspace  : {ws}")
        if instruction:
            typer.echo(f"  Instruction: {instruction}")
    except WorkspaceOccupiedError as err:
        typer.secho(f"Workspace error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except (AgentNotAvailableError, AgentNotFoundError, AgentStartError, AgentError) as err:
        typer.secho(f"Agent launch error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except Exception as err:
        typer.secho(f"Unexpected error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@agent_app.command(name="status")
def agent_status(
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Target project workspace directory.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to coderelay configuration file.",
    ),
) -> None:
    """Check active agent status and workspace ownership."""
    ws = _resolve_workspace(workspace, config_path)
    manager = _get_agent_manager(config_path)
    owner = manager.get_active_owner(ws)

    typer.echo(f"Workspace: {ws}")
    if owner is None:
        typer.echo("Active Agent: None (workspace is unoccupied)")
    else:
        typer.echo(
            f"Active Agent: {owner.agent_name} (PID: {owner.pid}, "
            f"acquired: {owner.acquired_at.strftime('%Y-%m-%d %H:%M:%S')})"
        )
        typer.secho("Status: Occupied and running", fg=typer.colors.GREEN)


@agent_app.command(name="stop")
def agent_stop(
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Target project workspace directory.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to coderelay configuration file.",
    ),
) -> None:
    """Stop the active agent in the workspace and release ownership."""
    ws = _resolve_workspace(workspace, config_path)
    manager = _get_agent_manager(config_path)
    stopped = manager.stop_agent(ws)

    if stopped:
        typer.secho(f"Agent stopped successfully. Workspace '{ws}' ownership released.", fg=typer.colors.GREEN)
    else:
        typer.echo(f"No active agent running in workspace '{ws}'.")


# ----------------------------------------------------------------------
# Project Commands
# ----------------------------------------------------------------------


@project_app.command(name="register")
def project_register(
    path: Path = typer.Argument(Path("."), help="Path to an existing local Git repository directory."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Optional custom name for the project."),
) -> None:
    """Register an existing local Git repository with CodeRelay."""
    reg = ProjectRegistry()
    try:
        project = reg.register_existing(path=path, name=name)
        typer.secho(f"Project '{project.name}' registered successfully.", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"  Path          : {project.path}")
        typer.echo(f"  Remote URL    : {project.remote_url or 'None (local only)'}")
        typer.echo(f"  Default Branch: {project.default_branch}")
        typer.echo(f"  Context File  : {project.context_file}")
    except (NotAGitRepositoryError, ProjectError, GitError) as err:
        typer.secho(f"Registration error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@project_app.command(name="clone")
def project_clone(
    url: str = typer.Argument(..., help="Git repository URL to clone (HTTPS or SSH)."),
    destination: Path = typer.Argument(..., help="Destination directory path to clone into."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Optional custom project name."),
) -> None:
    """Clone a Git repository and register it with CodeRelay."""
    reg = ProjectRegistry()
    try:
        typer.echo(f"Cloning '{url}' into '{destination}'...")
        project = reg.clone_and_register(url=url, destination=destination, name=name)
        typer.secho(f"Repository cloned and registered as '{project.name}'.", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"  Path          : {project.path}")
        typer.echo(f"  Remote URL    : {project.remote_url}")
        typer.echo(f"  Default Branch: {project.default_branch}")
    except (GitError, ProjectError) as err:
        typer.secho(f"Clone error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@app.command(name="projects")
@project_app.command(name="list")
def project_list() -> None:
    """List all registered CodeRelay projects."""
    reg = ProjectRegistry()
    projects = reg.list_projects()

    if not projects:
        typer.echo("No projects currently registered. Register one with 'coderelay project register <path>'.")
        return

    typer.echo("=== Registered Projects ===")
    for p in projects:
        exists_str = "exists" if p.path.is_dir() else "missing"
        typer.echo(f"- {p.name:<18} [{p.default_branch}] -> {p.path} ({exists_str})")
        if p.remote_url:
            typer.echo(f"    remote: {p.remote_url}")


@project_app.command(name="inspect")
def project_inspect(
    name_or_path: str = typer.Argument(..., help="Name or path of the registered project to inspect."),
) -> None:
    """Inspect details of a registered project."""
    reg = ProjectRegistry()
    proj = reg.get_project(name_or_path)
    if proj is None:
        typer.secho(f"Project '{name_or_path}' is not registered.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    is_dir = proj.path.is_dir()
    is_git = is_git_repo(proj.path) if is_dir else False
    ctx_file = proj.path / proj.context_file
    has_ctx = ctx_file.is_file() if is_dir else False

    typer.echo(f"=== Project: {proj.name} ===")
    typer.echo(f"  Path            : {proj.path}")
    typer.echo(f"  Directory exists: {'Yes' if is_dir else 'No'}")
    typer.echo(f"  Git repository  : {'Yes' if is_git else 'No'}")
    typer.echo(f"  Remote URL      : {proj.remote_url or 'None (local only)'}")
    typer.echo(f"  Default Branch  : {proj.default_branch}")
    typer.echo(f"  Context File    : {proj.context_file} ({'Present' if has_ctx else 'Missing'})")
    typer.echo(f"  Registered At   : {proj.registered_at.strftime('%Y-%m-%d %H:%M:%S')}")


@project_app.command(name="remove")
def project_remove(
    name_or_path: str = typer.Argument(..., help="Name or path of the project to remove from registry."),
) -> None:
    """Remove a project from the CodeRelay registry (preserves files on disk)."""
    reg = ProjectRegistry()
    removed = reg.remove_project(name_or_path)
    if removed:
        typer.secho(
            f"Project '{name_or_path}' removed from registry. Local files were preserved.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(f"Project '{name_or_path}' was not found in registry.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


# ----------------------------------------------------------------------
# Context Commands
# ----------------------------------------------------------------------


@context_app.command(name="init")
def context_init_cmd(
    workspace: Optional[Path] = typer.Option(None, "--workspace", "-w", help="Workspace path to initialize."),
    force: bool = typer.Option(False, "--force", "-f", help="Force overwrite existing context file."),
) -> None:
    """Initialize CODERELAY_CONTEXT.md in the project workspace."""
    ws = _resolve_workspace(workspace)
    try:
        ctx_path, created = init_context(ws, force=force)
        if created:
            typer.secho(f"Created context file at: {ctx_path}", fg=typer.colors.GREEN, bold=True)
        else:
            typer.echo(f"Preserved existing context file at: {ctx_path}")
    except Exception as err:
        typer.secho(f"Context initialization error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@context_app.command(name="show")
def context_show_cmd(
    workspace: Optional[Path] = typer.Option(None, "--workspace", "-w", help="Workspace path to inspect."),
) -> None:
    """Read and display the project context file."""
    ws = _resolve_workspace(workspace)
    try:
        content = read_context(ws)
        typer.echo(content)
    except ContextError as err:
        typer.secho(f"Context error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@context_app.command(name="update")
def context_update_cmd(
    section: str = typer.Argument(..., help="Section title to update (e.g. 'Current Task and Progress')."),
    content: str = typer.Argument(..., help="New section markdown content."),
    workspace: Optional[Path] = typer.Option(None, "--workspace", "-w", help="Workspace path."),
    append: bool = typer.Option(False, "--append", "-a", help="Append content instead of replacing section body."),
) -> None:
    """Update a specific section in CODERELAY_CONTEXT.md while preserving others."""
    ws = _resolve_workspace(workspace)
    try:
        update_context_section(ws, section_name=section, new_content=content, append=append)
        typer.secho(f"Section '{section}' updated successfully in {CONTEXT_FILENAME}.", fg=typer.colors.GREEN)
    except Exception as err:
        typer.secho(f"Error updating context: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@context_app.command(name="validate")
def context_validate_cmd(
    workspace: Optional[Path] = typer.Option(None, "--workspace", "-w", help="Workspace path to validate."),
) -> None:
    """Validate that required sections are present in CODERELAY_CONTEXT.md."""
    ws = _resolve_workspace(workspace)
    try:
        content = read_context(ws)
        valid, missing = validate_context(content)
        if valid:
            typer.secho(f"Context file '{CONTEXT_FILENAME}' is valid. All required sections present.", fg=typer.colors.GREEN)
        else:
            typer.secho(f"Context file is missing {len(missing)} required section(s):", fg=typer.colors.RED, err=True)
            for m in missing:
                typer.echo(f"  - {m}")
            raise typer.Exit(code=1)
    except ContextError as err:
        typer.secho(f"Context error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


# ----------------------------------------------------------------------
# Git Commands
# ----------------------------------------------------------------------


@git_app.command(name="status")
def git_status_cmd(
    workspace: Optional[Path] = typer.Option(None, "--workspace", "-w", help="Workspace path to check."),
) -> None:
    """Check working tree, branch, and remote tracking status."""
    ws = _resolve_workspace(workspace)
    try:
        st = get_git_status(ws)
        typer.echo("=== Git Status ===")
        typer.echo(f"Branch       : {st.current_branch}")
        typer.echo(f"Upstream     : {st.tracking_branch or 'None'}")
        typer.echo(f"Ahead/Behind : {st.ahead} ahead, {st.behind} behind")
        clean_color = typer.colors.GREEN if st.is_clean else typer.colors.YELLOW
        typer.echo("Working Tree : " + typer.style("Clean" if st.is_clean else "Uncommitted changes", fg=clean_color))

        if st.staged_files:
            typer.echo(f"Staged ({len(st.staged_files)}):")
            for f in st.staged_files:
                typer.echo(f"  + {f}")
        if st.unstaged_files:
            typer.echo(f"Unstaged ({len(st.unstaged_files)}):")
            for f in st.unstaged_files:
                typer.echo(f"  * {f}")
        if st.conflicted_files:
            typer.secho(f"Conflicts ({len(st.conflicted_files)}):", fg=typer.colors.RED)
            for f in st.conflicted_files:
                typer.echo(f"  ! {f}")
        if st.untracked_files:
            typer.echo(f"Untracked ({len(st.untracked_files)}):")
            for f in st.untracked_files[:10]:
                typer.echo(f"  ? {f}")
            if len(st.untracked_files) > 10:
                typer.echo(f"  ... and {len(st.untracked_files) - 10} more")
    except (NotAGitRepositoryError, GitError) as err:
        typer.secho(f"Git error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@git_app.command(name="fetch")
def git_fetch_cmd(
    workspace: Optional[Path] = typer.Option(None, "--workspace", "-w", help="Workspace path."),
    remote: str = typer.Option("origin", "--remote", "-r", help="Remote name to fetch from."),
) -> None:
    """Fetch updates from remote repository."""
    ws = _resolve_workspace(workspace)
    try:
        typer.echo(f"Fetching from remote '{remote}'...")
        fetch_remote(ws, remote=remote)
        typer.secho(f"Fetch completed successfully from '{remote}'.", fg=typer.colors.GREEN)
    except (NotAGitRepositoryError, GitError) as err:
        typer.secho(f"Fetch error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@git_app.command(name="sync")
def git_sync_cmd(
    workspace: Optional[Path] = typer.Option(None, "--workspace", "-w", help="Workspace path to synchronize."),
    remote: str = typer.Option("origin", "--remote", "-r", help="Remote name."),
) -> None:
    """Safely synchronize workspace with remote (fast-forward only)."""
    ws = _resolve_workspace(workspace)
    service = GitSyncService(ws, remote=remote)
    try:
        res = service.synchronize()
        if res.status == SyncStatus.UP_TO_DATE:
            typer.secho(f"[Up to date] {res.message}", fg=typer.colors.GREEN)
        elif res.status == SyncStatus.SYNCHRONIZED:
            typer.secho(f"[Synchronized] {res.message}", fg=typer.colors.GREEN, bold=True)
        elif res.status == SyncStatus.DIVERGED:
            typer.secho(f"[Diverged] {res.message}", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)
        elif res.status == SyncStatus.NO_REMOTE:
            typer.echo(f"[No Remote] {res.message}")
    except (UnsafeSyncError, MergeConflictError, GitSyncError, GitError) as err:
        typer.secho(f"Sync error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
