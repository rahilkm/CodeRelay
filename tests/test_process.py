"""Tests for Codex process management."""

from unittest.mock import MagicMock, patch

import pytest

from coderelay.exceptions import CodexNotFoundError, CodexProcessError
from coderelay.process import locate_codex_cli, start_codex_app_server, terminate_codex_process


def test_locate_codex_cli_found():
    with patch("shutil.which", return_value="C:\\dummy\\codex.cmd"):
        path = locate_codex_cli()
        assert path == "C:\\dummy\\codex.cmd"


def test_locate_codex_cli_not_found():
    with patch("shutil.which", return_value=None):
        with pytest.raises(CodexNotFoundError):
            locate_codex_cli("non_existent_codex")


def test_start_codex_app_server_invocation():
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        proc = start_codex_app_server("C:\\fake\\codex.cmd")
        assert proc is mock_proc
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert '"C:\\fake\\codex.cmd" app-server' in args[0]
        assert kwargs["shell"] is True


def test_start_codex_app_server_immediate_exit():
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    mock_proc.returncode = 1

    with patch("subprocess.Popen", return_value=mock_proc):
        with pytest.raises(CodexProcessError, match="terminated immediately"):
            start_codex_app_server("C:\\fake\\codex.cmd")


def test_terminate_codex_process_windows():
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.poll.side_effect = [None, 0]  # Initially running, then terminated

    with patch("sys.platform", "win32"), patch("subprocess.run") as mock_run:
        terminate_codex_process(mock_proc)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "taskkill" in cmd
        assert "/T" in cmd
        assert "12345" in cmd
