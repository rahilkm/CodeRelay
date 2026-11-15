"""Windows process management for the Codex App Server."""

from __future__ import annotations

import ctypes
import logging
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Optional

from coderelay.exceptions import CodexNotFoundError, CodexProcessError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DiscoveredProcess:
    """Represents a discovered OS process matched to a workspace."""

    pid: int
    name: str
    command_line: str
    cwd: Path


def get_process_info(pid: int) -> tuple[Optional[Path], Optional[str]]:
    """Retrieve the working directory (CWD) and command line for a running process.

    Uses native NT APIs via ctypes on Windows without external dependencies.
    Returns (cwd_path, command_line_str) or (None, None) if inaccessible.
    """
    if pid <= 0 or pid == os.getpid():
        return None, None

    if sys.platform == "win32":
        try:
            kernel32 = ctypes.WinDLL("kernel32.dll")
            ntdll = ctypes.WinDLL("ntdll.dll")

            class PROCESS_BASIC_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("ExitStatus", ctypes.c_ulonglong),
                    ("PebBaseAddress", ctypes.c_void_p),
                    ("AffinityMask", ctypes.c_ulonglong),
                    ("BasePriority", ctypes.c_ulonglong),
                    ("UniqueProcessId", ctypes.c_ulonglong),
                    ("InheritedFromUniqueProcessId", ctypes.c_ulonglong),
                ]

            PROCESS_QUERY_INFORMATION = 0x0400
            PROCESS_VM_READ = 0x0010
            h_proc = kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
            )
            if not h_proc:
                return None, None

            try:
                pbi = PROCESS_BASIC_INFORMATION()
                ret_len = ctypes.c_ulong()
                status = ntdll.NtQueryInformationProcess(
                    h_proc, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len)
                )
                if status != 0 or not pbi.PebBaseAddress:
                    return None, None

                proc_params_addr = ctypes.c_void_p()
                if not kernel32.ReadProcessMemory(
                    h_proc,
                    ctypes.c_void_p(pbi.PebBaseAddress + 0x20),
                    ctypes.byref(proc_params_addr),
                    8,
                    None,
                ) or not proc_params_addr.value:
                    return None, None

                # 1. Read CurrentDirectory (CURDIR at proc_params + 0x38)
                curdir_buf = (ctypes.c_ubyte * 16)()
                kernel32.ReadProcessMemory(
                    h_proc,
                    ctypes.c_void_p(proc_params_addr.value + 0x38),
                    curdir_buf,
                    16,
                    None,
                )
                cwd_len = int.from_bytes(curdir_buf[0:2], "little")
                cwd_ptr = int.from_bytes(curdir_buf[8:16], "little")
                cwd_path: Optional[Path] = None
                if cwd_ptr and cwd_len > 0:
                    path_buf = (ctypes.c_ubyte * cwd_len)()
                    if kernel32.ReadProcessMemory(
                        h_proc, ctypes.c_void_p(cwd_ptr), path_buf, cwd_len, None
                    ):
                        raw_cwd = bytes(path_buf).decode("utf-16le", errors="ignore").rstrip("\x00")
                        if raw_cwd:
                            cwd_path = Path(raw_cwd).expanduser().resolve()

                # 2. Read CommandLine (UNICODE_STRING at proc_params + 0x70)
                cmd_buf = (ctypes.c_ubyte * 16)()
                kernel32.ReadProcessMemory(
                    h_proc,
                    ctypes.c_void_p(proc_params_addr.value + 0x70),
                    cmd_buf,
                    16,
                    None,
                )
                cmd_len = int.from_bytes(cmd_buf[0:2], "little")
                cmd_ptr = int.from_bytes(cmd_buf[8:16], "little")
                cmdline: Optional[str] = None
                if cmd_ptr and cmd_len > 0:
                    str_buf = (ctypes.c_ubyte * cmd_len)()
                    if kernel32.ReadProcessMemory(
                        h_proc, ctypes.c_void_p(cmd_ptr), str_buf, cmd_len, None
                    ):
                        cmdline = bytes(str_buf).decode("utf-16le", errors="ignore").rstrip("\x00")

                return cwd_path, cmdline
            finally:
                kernel32.CloseHandle(h_proc)
        except Exception as err:
            logger.debug("Failed to inspect process %s via NT API: %s", pid, err)
            return None, None
    else:
        # POSIX fallback using /proc
        proc_dir = Path(f"/proc/{pid}")
        if proc_dir.is_dir():
            cwd_link = proc_dir / "cwd"
            cmd_file = proc_dir / "cmdline"
            cwd_val: Optional[Path] = None
            cmd_val: Optional[str] = None
            try:
                if cwd_link.is_symlink():
                    cwd_val = cwd_link.resolve()
            except Exception:
                pass
            try:
                if cmd_file.is_file():
                    raw = cmd_file.read_bytes()
                    cmd_val = " ".join(raw.decode("utf-8", errors="ignore").split("\x00")).strip()
            except Exception:
                pass
            return cwd_val, cmd_val

    return None, None


def find_codex_processes(
    workspace: Path,
    command_pattern: str = "codex",
) -> list[DiscoveredProcess]:
    """Discover all active processes running Codex inside the specified workspace."""
    resolved_ws = workspace.expanduser().resolve()
    matches: list[DiscoveredProcess] = []

    if sys.platform == "win32":
        try:
            import ctypes.wintypes as wt

            TH32CS_SNAPPROCESS = 0x00000002

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wt.DWORD),
                    ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260),
                ]

            kernel32 = ctypes.WinDLL("kernel32.dll")
            h_snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if not h_snap or h_snap == -1:
                return []

            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)

            try:
                if kernel32.Process32FirstW(h_snap, ctypes.byref(entry)):
                    while True:
                        pid = entry.th32ProcessID
                        exe_name = entry.szExeFile
                        if pid > 4 and pid != os.getpid():
                            cwd, cmdline = get_process_info(pid)
                            if cwd is not None and cwd == resolved_ws:
                                # Check if executable name or commandline matches codex
                                target_patt = command_pattern.lower()
                                is_match = (
                                    target_patt in exe_name.lower()
                                    or (cmdline and target_patt in cmdline.lower())
                                )
                                if is_match:
                                    matches.append(
                                        DiscoveredProcess(
                                            pid=pid,
                                            name=exe_name,
                                            command_line=cmdline or "",
                                            cwd=cwd,
                                        )
                                    )
                        if not kernel32.Process32NextW(h_snap, ctypes.byref(entry)):
                            break
            finally:
                kernel32.CloseHandle(h_snap)
        except Exception as err:
            logger.debug("Process enumeration error: %s", err)
    else:
        # POSIX enumeration via /proc
        proc_root = Path("/proc")
        if proc_root.is_dir():
            for p in proc_root.iterdir():
                if p.name.isdigit():
                    pid = int(p.name)
                    if pid != os.getpid():
                        cwd, cmdline = get_process_info(pid)
                        if cwd is not None and cwd == resolved_ws:
                            target_patt = command_pattern.lower()
                            if cmdline and target_patt in cmdline.lower():
                                matches.append(
                                    DiscoveredProcess(
                                        pid=pid,
                                        name=target_patt,
                                        command_line=cmdline,
                                        cwd=cwd,
                                    )
                                )

    return matches


def locate_codex_cli(custom_command: Optional[str] = None) -> str:
    """Locate the Codex CLI executable on Windows."""
    target = custom_command or "codex"
    found = shutil.which(target)
    if not found:
        raise CodexNotFoundError(
            f"Codex CLI '{target}' was not found in PATH. Please ensure Codex CLI is installed."
        )
    return found


def start_codex_app_server(
    codex_path: str,
    stderr: int | None = subprocess.DEVNULL,
) -> subprocess.Popen:
    """Start the Codex App Server preserving the verified Windows process-launching approach.

    Do not replace this working invocation with subprocess.Popen(["codex", ...]).
    """
    cmd = f'"{codex_path}" app-server'
    executable = os.environ.get("COMSPEC", "cmd.exe")

    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            executable=executable,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
    except Exception as err:
        raise CodexProcessError(f"Failed to start Codex App Server: {err}") from err

    if proc.poll() is not None:
        raise CodexProcessError(
            f"Codex App Server process terminated immediately with exit code {proc.returncode}."
        )

    return proc


def terminate_codex_process(proc: subprocess.Popen, timeout: float = 3.0) -> None:
    """Terminate the Codex App Server and its child processes completely to avoid orphans."""
    if proc is None:
        return

    pid = proc.pid
    if proc.poll() is not None:
        # Process already terminated, ensure pipes are closed
        _close_pipes(proc)
        return

    # On Windows, shell=True runs cmd.exe which spawns the node/codex child.
    # taskkill /F /T terminates the process and any child processes it started.
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except Exception as e:
            logger.debug("taskkill failed for pid %s: %s", pid, e)

    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=1.0)
    except Exception:
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=1.0)
        except Exception:
            pass
    finally:
        _close_pipes(proc)


def _close_pipes(proc: subprocess.Popen) -> None:
    """Safely close stdin/stdout/stderr pipes of a process."""
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None and not pipe.closed:
            try:
                pipe.close()
            except Exception:
                pass
