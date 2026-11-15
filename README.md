# CodeRelay

### AI Coding Agent Orchestration & Intelligent Handoff System

CodeRelay is a command-line orchestration tool designed to coordinate multiple AI coding agents, monitor their usage, and transfer development tasks between agents while preserving project context and development progress.

It currently integrates **OpenAI Codex CLI** as the primary coding agent and **Google Antigravity CLI** as the secondary agent.

The goal is to reduce manual coordination between coding agents by automating usage monitoring, context preservation, Git synchronization, and agent handoff.

---

## Table of Contents

1. [Overview](#overview)
2. [Problem Statement](#problem-statement)
3. [Key Features](#key-features)
4. [Architecture](#architecture)
5. [Technology Stack](#technology-stack)
6. [Prerequisites](#prerequisites)
7. [Installation](#installation)
8. [Configuration](#configuration)
9. [Getting Started](#getting-started)
10. [Usage and CLI Commands](#usage-and-cli-commands)
11. [Agent Handoff Workflow](#agent-handoff-workflow)
12. [Git Integration](#git-integration)
13. [Project Context Management](#project-context-management)
14. [Testing](#testing)
15. [Project Structure](#project-structure)
16. [Safety Mechanisms](#safety-mechanisms)
17. [Known Limitations](#known-limitations)
18. [Future Improvements](#future-improvements)

---

## 1. Overview

AI coding agents can independently write code, debug applications, execute tests, and manage development workflows. However, developers working with multiple agents often need to manually coordinate their usage, transfer context, and synchronize project changes.

CodeRelay addresses this problem by providing a centralized orchestration layer.

It operates independently of the coding agents and allows developers to continue interacting directly with their preferred agent while CodeRelay monitors usage in the background.

When a handoff is required, CodeRelay coordinates the transition from one agent to another.

## 2. Problem Statement

Using multiple AI coding agents introduces several challenges:

- Usage limits can interrupt ongoing development.
- Switching agents requires manually transferring project context.
- The incoming agent may not understand previous implementation decisions.
- Git changes must be synchronized safely.
- Multiple agents must not modify the same workspace simultaneously.
- Developers need control over when an agent transition occurs.

CodeRelay aims to solve these problems through automated monitoring and structured agent handoff.

## 3. Key Features

### Usage Monitoring
- Connects to the Codex App Server through JSON-RPC.
- Retrieves account usage and rate-limit information.
- Continuously monitors usage at configurable polling intervals.
- Displays usage percentages and reset information.

### Intelligent Threshold Detection
- 90% usage: Switch recommendation.
- 95% usage: Critical switch recommendation.
- Suppresses repeated recommendations after a declined switch until the critical threshold is reached.

### Multiple Agent Integration
- OpenAI Codex CLI integration.
- Google Antigravity CLI integration.
- Common agent management interface.
- Workspace ownership tracking.

### Context Preservation
- Maintains a project-specific `CODERELAY_CONTEXT.md`.
- Records completed tasks, current progress, pending work, technical decisions, and continuation instructions.
- Validates required context sections before handoff.

### Git-Based Synchronization
- Uses the Git CLI for repository operations.
- Supports repository status checks and synchronization.
- Uses fast-forward-only synchronization.
- Creates handoff commits and verifies synchronization with the configured remote.

### Safe Agent Handoff
- Requires explicit user approval.
- Confirms outgoing task completion.
- Validates project context.
- Runs available tests.
- Inspects Git changes.
- Prevents simultaneous agent ownership.
- Launches the incoming agent with continuation instructions.

---

## 4. Architecture

CodeRelay acts as an orchestration layer between the developer, coding agents, and Git repositories.

```text
                 DEVELOPER
                     |
          +----------+----------+
          |                     |
     Codex CLI              CodeRelay CLI
          |                     |
          |              Usage Monitoring
          |                     |
          |              Threshold Detection
          |                     |
          |              Approval Manager
          |                     |
          |              Handoff Engine
          |                     |
          |          +----------+----------+
          |          |                     |
          |     Context Manager        Git Manager
          |          |                     |
          |          +----------+----------+
          |                     |
          +--------------- Workspace
                                |
                         Agent Management
                                |
                         Antigravity CLI
```

### Main Components

| Component | Responsibility |
|---|---|
| Usage Monitor | Retrieves and monitors Codex usage |
| Threshold Detector | Identifies usage thresholds |
| Agent Manager | Manages coding agent processes |
| Workspace Manager | Tracks workspace ownership |
| Context Manager | Maintains project context |
| Git Manager | Handles repository operations |
| Handoff Service | Coordinates outgoing and incoming transitions |
| Orchestrator | Connects all components into one workflow |
| CLI | Provides user-facing commands |

---

## 5. Technology Stack

| Technology | Purpose |
|---|---|
| Python 3.14 | Core application development |
| Typer | Command-line interface |
| Pydantic | Configuration and data validation |
| JSON-RPC | Communication with Codex App Server |
| Subprocess | External CLI and process management |
| Git CLI | Version control and synchronization |
| Pytest | Automated testing |
| OpenAI Codex CLI | Primary coding agent |
| Google Antigravity CLI | Secondary coding agent |

The implementation prioritizes Python's standard library wherever practical.

---

## 6. Prerequisites

Install the following before setting up CodeRelay:

1. Python 3.14 or a compatible supported Python version.
2. Git.
3. OpenAI Codex CLI.
4. Google Antigravity CLI.
5. An authenticated Codex account.
6. An authenticated Antigravity environment.
7. Windows PowerShell or another supported terminal.

Verify the installations:

```powershell
python --version
git --version
codex --version
agy --version
```

---

## 7. Installation

### Step 1: Clone the repository

```powershell
git clone https://github.com/rahilkm/CodeRelay.git
```

### Step 2: Navigate into the project

```powershell
cd CodeRelay
```

### Step 3: Create a virtual environment

```powershell
python -m venv .venv
```

Activate it in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks script execution, use an appropriate execution policy for your environment or activate the environment through another supported shell.

### Step 4: Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -e .
```

If the project is configured to use the requirements file instead, install its dependencies with:

```powershell
pip install -r requirements.txt
```

### Step 5: Verify installation

```powershell
coderelay --version
coderelay --help
```

You can also run the module directly:

```powershell
python -m coderelay --help
```

---

## 8. Configuration

CodeRelay uses a JSON configuration file.

The repository includes an example configuration:

```text
coderelay.example.json
```

Create your local configuration using the example:

```powershell
Copy-Item coderelay.example.json coderelay.json
```

Edit `coderelay.json` according to your environment and the configuration options supported by the project.

**Important:** Do not commit `coderelay.json` if it contains local settings, credentials, or sensitive information.

The repository's `.gitignore` excludes the local configuration file.

Use the configuration option when running commands:

```powershell
coderelay status --config .\coderelay.json
```

---

## 9. Getting Started

### Step 1: Verify CodeRelay

```powershell
coderelay status
```

This checks the configured environment and project status.

### Step 2: Register a project

Navigate to your existing Git repository:

```powershell
cd C:\path\to\your\project
```

Register it:

```powershell
coderelay project register . --name MyProject
```

### Step 3: Initialize project context

```powershell
coderelay context init --workspace "C:\path\to\your\project"
```

### Step 4: Validate context

```powershell
coderelay context validate --workspace "C:\path\to\your\project"
```

### Step 5: Start the coding agent

Open Codex in the project workspace:

```powershell
codex
```

Continue interacting with Codex normally.

### Step 6: Start CodeRelay monitoring

Open another terminal:

```powershell
coderelay monitor --project "C:\path\to\your\project" --config "C:\path\to\CodeRelay\coderelay.json"
```

CodeRelay will connect to the Codex App Server and monitor account usage independently.

---

## 10. Usage and CLI Commands

### General Commands

```powershell
coderelay --help
coderelay --version
coderelay status
```

### Agent Management

List available agents:

```powershell
coderelay agents
```

List or inspect agent information:

```powershell
coderelay agent list
coderelay agent status
```

Start an agent:

```powershell
coderelay agent start codex
```

Stop an agent:

```powershell
coderelay agent stop
```

### Monitoring

```powershell
coderelay monitor
```

Monitoring can also be started for a specific project:

```powershell
coderelay monitor --project "C:\path\to\project"
```

### Project Management

```powershell
coderelay project --help
```

### Context Management

```powershell
coderelay context --help
```

### Git Operations

```powershell
coderelay git --help
```

Available Git operations include:

- `status`
- `fetch`
- `sync`

For exact options supported by your installed version, use the individual command's `--help` option.

### Manual Switching

Manually request an agent switch:

```powershell
coderelay switch --project "C:\path\to\project" --to antigravity
```

To switch back:

```powershell
coderelay switch --project "C:\path\to\project" --to codex
```

---

## 11. Agent Handoff Workflow

CodeRelay supports two ways of initiating a handoff.

### A. Automatic Handoff

Automatic handoff is triggered when the usage monitor detects that an agent has reached a configured threshold.

The workflow:

1. Codex usage is monitored.
2. Usage reaches 90%.
3. CodeRelay recommends switching agents.
4. The developer approves or declines.
5. At 95%, a critical recommendation is generated if switching was previously declined.
6. CodeRelay confirms task completion.
7. The project context is validated.
8. Available tests are executed.
9. Git changes are inspected and committed.
10. Changes are synchronized with the configured remote.
11. The outgoing agent is stopped.
12. Workspace ownership is transferred.
13. The incoming agent is launched with continuation instructions.

### B. Manual Handoff

Developers can initiate a switch without waiting for a usage threshold.

Example:

```powershell
coderelay switch --project "C:\path\to\project" --to antigravity
```

The same handoff safety checks apply.

### Context Continuity

The incoming agent receives instructions to:

- Read `CODERELAY_CONTEXT.md`.
- Inspect the existing project implementation.
- Understand completed work.
- Identify pending tasks.
- Continue development without requiring the developer to repeat the entire original task.

---

## 12. Git Integration

CodeRelay uses the Git CLI rather than a separate GitHub REST API integration.

Git operations are used to preserve project consistency during agent transitions.

### Synchronization Process

```text
Outgoing Agent
      |
Inspect Changes
      |
Validate Workspace
      |
Stage Approved Files
      |
Create Commit
      |
Push to Configured Remote
      |
Verify Remote State
      |
Release Workspace
      |
Incoming Agent
      |
Fast-forward Synchronization
      |
Continue Development
```

### Local Git Repository

Every managed project should be a Git repository.

Initialize a repository:

```powershell
git init
```

Configure a remote:

```powershell
git remote add origin <repository-url>
```

For a local-only Git remote, a bare repository can be created:

```powershell
git init --bare C:\path\to\CodeRelayLocalRemote.git
```

Then configure it from the application workspace:

```powershell
git remote add origin C:\path\to\CodeRelayLocalRemote.git
```

This stores the Git remote on the same machine rather than on a cloud hosting service.

---

## 13. Project Context Management

Each managed project contains:

```text
CODERELAY_CONTEXT.md
```

This file is essential to preserving development continuity.

It contains:

- Project Overview
- Repository Information
- Tech Stack and Frameworks
- Project Architecture
- Important Directories and Files
- Completed Tasks
- Current Task and Progress
- Pending Tasks
- Technical Decisions
- Known Issues
- Testing Instructions and Results
- Next Agent Instructions

Agents should update this file before completing a handoff.

CodeRelay validates the required sections before proceeding.

---

## 14. Testing

CodeRelay uses Pytest for automated testing.

Run the complete test suite:

```powershell
python -m pytest -q
```

Run a specific test file:

```powershell
python -m pytest tests/test_handoff.py -v
```

Run the two-terminal workflow tests:

```powershell
python -m pytest tests/test_two_terminal_workflow.py -v
```

Run tests with detailed output:

```powershell
python -m pytest -v
```

Tests cover areas including:

- CLI behavior
- Usage parsing
- Threshold detection
- Agent management
- Workspace ownership
- Git operations
- Context validation
- Handoff approval
- Outgoing handoff
- Incoming handoff
- Process discovery
- Two-terminal monitoring and handoff workflows

A project without a discoverable test suite should be reported as having tests skipped, rather than being treated as having failed tests. Actual test failures must still block handoff.

---

## 15. Project Structure

```text
CodeRelay/
│
├── coderelay/
│   ├── agents/
│   │   ├── base.py
│   │   ├── codex.py
│   │   ├── antigravity.py
│   │   ├── manager.py
│   │   ├── registry.py
│   │   └── workspace.py
│   │
│   ├── orchestrator/
│   │   ├── approval.py
│   │   ├── handoff.py
│   │   ├── orchestrator.py
│   │   └── state.py
│   │
│   ├── context.py
│   ├── git.py
│   ├── monitor.py
│   ├── process.py
│   ├── projects.py
│   ├── rpc.py
│   ├── sync.py
│   ├── threshold_detector.py
│   ├── usage_models.py
│   ├── usage_parser.py
│   └── cli.py
│
├── tests/
│
├── pyproject.toml
├── requirements.txt
├── coderelay.example.json
├── .gitignore
└── README.md
```

The exact files may evolve as the implementation develops.

---

## 16. Safety Mechanisms

CodeRelay is designed to prevent unsafe transitions between coding agents.

### Explicit Approval
Every agent switch requires developer approval.

### Workspace Ownership
Only one coding agent should own and modify a workspace at a time.

### Context Validation
Handoff is blocked when required project context sections are missing.

### Git Safety
- No force pushes.
- No destructive resets.
- No blind staging of all project files.
- No automatic deletion of user changes.
- Protected files and sensitive configuration are excluded from handoff commits.
- Synchronization uses fast-forward-only operations.

### Test Verification
Available project tests are executed before handoff. Actual test failures prevent the transition.

### Failure Recovery
If Git synchronization fails, CodeRelay should retain the local commit and report the recovery instructions rather than silently losing work.

---

## 17. Known Limitations

- Codex usage monitoring depends on the Codex App Server interface.
- Process discovery and termination depend on operating-system process behavior.
- Forceful termination may be necessary when an outgoing agent does not stop cleanly.
- Multiple Codex sessions associated with the same workspace require careful process selection.
- Interactive agent behavior depends on the installed CLI version.
- Git credentials and remote access must already be configured.
- The complete handoff workflow should be validated with the specific project and environment before relying on it for production development.

---

## 18. Future Improvements

Potential future enhancements include:

- Improved cross-platform process discovery.
- More reliable interactive process lifecycle management.
- Better recovery from interrupted handoffs.
- Expanded support for additional AI coding agents.
- More robust workspace ownership recovery.
- Improved automatic context generation.
- Configurable usage thresholds.
- Detailed handoff audit logs.
- Enhanced monitoring and observability.
- More comprehensive integration testing.

---

## Contributing

Contributions, bug reports, and feature suggestions are welcome.

When contributing:

1. Create a separate feature branch.
2. Follow the existing modular project structure.
3. Add tests for new functionality.
4. Run the complete test suite.
5. Update documentation when behavior changes.
6. Submit a pull request with a clear description.

---

## License

Add the project's chosen license before distributing or accepting external contributions.

---

## Repository

**GitHub:** https://github.com/rahilkm/CodeRelay

**Project:** CodeRelay — AI Coding Agent Orchestration System

**Primary Integrations:** OpenAI Codex CLI and Google Antigravity CLI

