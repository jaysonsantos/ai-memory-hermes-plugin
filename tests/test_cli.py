from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_register_cli_creates_subcommands() -> None:
    """Hermes passes the `hermes ai-memory` ArgumentParser, not a
    _SubParsersAction; add_parser() on it raised AttributeError and aborted
    plugin CLI registration for every plugin."""
    from cli import register_cli

    parser = argparse.ArgumentParser(prog="ai-memory")
    register_cli(parser)

    for argv in (["status"], ["config"], ["config-set", "k", "v"], ["link"], ["update"]):
        assert parser.parse_args(argv).ai_memory_command == argv[0]


def test_register_cli_sets_top_level_handler() -> None:
    from cli import ai_memory_command, register_cli

    parser = argparse.ArgumentParser(prog="ai-memory")
    register_cli(parser)
    assert parser.parse_args([]).func is ai_memory_command


def test_config_set_arguments_are_parsed() -> None:
    from cli import register_cli

    parser = argparse.ArgumentParser(prog="ai-memory")
    register_cli(parser)
    args = parser.parse_args(["config-set", "workspace", "team"])
    assert (args.key, args.value) == ("workspace", "team")


def test_cmd_status_reachable(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from cli import cmd_status

    mock_client = MagicMock()
    mock_client.status.return_value = {"ok": True, "pages": 10, "sessions": 5}

    with patch("cli.AiMemoryClient", return_value=mock_client):
        args = argparse.Namespace(hermes_home=str(tmp_path))
        cmd_status(args)

    captured = capsys.readouterr()
    assert "reachable" in captured.out
    assert "Pages:    10" in captured.out
    assert "Sessions: 5" in captured.out


def test_cmd_status_reads_nested_counts(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """ai-memory 2.1.0 nests the counters under "counts"; reading the flat keys
    printed "Pages: ?" against every current server."""
    from cli import cmd_status

    mock_client = MagicMock()
    mock_client.status.return_value = {
        "version": "2.1.0",
        "counts": {"pages_latest": 59, "pages_all": 60, "sessions": 14},
    }

    with patch("cli.AiMemoryClient", return_value=mock_client):
        cmd_status(argparse.Namespace(hermes_home=str(tmp_path)))

    captured = capsys.readouterr()
    assert "Version:  2.1.0" in captured.out
    assert "Pages:    59" in captured.out
    assert "Sessions: 14" in captured.out
    assert "?" not in captured.out


def test_cmd_status_unreachable(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from cli import cmd_status

    mock_client = MagicMock()
    mock_client.status.side_effect = ConnectionError("Server refused connection")

    with patch("cli.AiMemoryClient", return_value=mock_client):
        args = argparse.Namespace(hermes_home=str(tmp_path))
        cmd_status(args)

    captured = capsys.readouterr()
    assert "unreachable" in captured.out.lower() or "error" in captured.out.lower()


def test_cmd_config_shows_values(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cli import cmd_config

    monkeypatch.delenv("AI_MEMORY_SERVER_URL", raising=False)
    monkeypatch.delenv("AI_MEMORY_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AI_MEMORY_API_KEY", raising=False)
    config_dir = tmp_path / ".hermes"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "ai-memory.json"
    config_file.write_text(
        json.dumps({"server_url": "http://custom:49374", "project": "custom-project"})
    )

    args = argparse.Namespace(hermes_home=str(config_dir))
    cmd_config(args)

    captured = capsys.readouterr()
    assert "http://custom:49374" in captured.out
    assert "custom-project" in captured.out
    assert "(not set)" in captured.out


def test_cmd_config_shows_env_source(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cli import cmd_config

    monkeypatch.setenv("AI_MEMORY_AUTH_TOKEN", "env-token-123")
    monkeypatch.delenv("AI_MEMORY_API_KEY", raising=False)
    config_dir = tmp_path / ".hermes"
    config_dir.mkdir(parents=True)
    (config_dir / "ai-memory.json").write_text(json.dumps({"server_url": "http://x:1"}))

    args = argparse.Namespace(hermes_home=str(config_dir))
    cmd_config(args)

    captured = capsys.readouterr()
    assert "set via env: AI_MEMORY_AUTH_TOKEN" in captured.out


def test_cmd_config_set_rejects_secrets(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from cli import cmd_config_set

    args = argparse.Namespace(hermes_home=str(tmp_path), key="auth_token", value="secret123")
    cmd_config_set(args)

    captured = capsys.readouterr()
    assert "NOT WRITTEN TO DISK" in captured.out
    assert "AI_MEMORY_AUTH_TOKEN" in captured.out
    # The value must never reach stdout: it would land in scrollback, terminal
    # logs and any session recording.
    assert "secret123" not in captured.out
    # Ensure nothing was written to disk
    assert not (tmp_path / "ai-memory.json").exists()


def test_cmd_config_set_allows_non_secrets(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from cli import cmd_config_set

    args = argparse.Namespace(hermes_home=str(tmp_path), key="workspace", value="my-ws")
    cmd_config_set(args)

    captured = capsys.readouterr()
    assert "saved" in captured.out
    data = json.loads((tmp_path / "ai-memory.json").read_text())
    assert data["workspace"] == "my-ws"


def test_cmd_config_set_rejects_api_key(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from cli import cmd_config_set

    args = argparse.Namespace(hermes_home=str(tmp_path), key="api_key", value="key-abc")
    cmd_config_set(args)

    captured = capsys.readouterr()
    assert "NOT WRITTEN TO DISK" in captured.out
    assert "AI_MEMORY_API_KEY" in captured.out
    assert "key-abc" not in captured.out


def test_cmd_config_missing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cli import cmd_config

    monkeypatch.delenv("AI_MEMORY_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AI_MEMORY_API_KEY", raising=False)
    args = argparse.Namespace(hermes_home=str(tmp_path / ".hermes"))
    cmd_config(args)

    captured = capsys.readouterr()
    assert captured.out
    assert "(not set)" in captured.out


def test_cmd_link_creates_symlink(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from cli import cmd_link

    plugin_dir = tmp_path / "plugins" / "memory" / "ai-memory"
    plugin_dir.mkdir(parents=True)

    hermes_plugins = tmp_path / "hermes" / "plugins"
    hermes_plugins.mkdir(parents=True)

    with (
        patch("cli.PLUGIN_DIR", str(plugin_dir)),
        patch("cli.os.path.islink", return_value=False),
        patch("cli.os.symlink") as mock_symlink,
    ):
        args = argparse.Namespace(hermes_home=str(hermes_plugins.parent))
        cmd_link(args)

    mock_symlink.assert_called_once()
    captured = capsys.readouterr()
    assert "linked" in captured.out.lower()


def test_cmd_link_already_linked(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from cli import cmd_link

    plugin_dir = tmp_path / "plugins" / "memory" / "ai-memory"
    plugin_dir.mkdir(parents=True)

    hermes_plugins = tmp_path / "hermes" / "plugins"
    hermes_plugins.mkdir(parents=True)

    with (
        patch("cli.PLUGIN_DIR", str(plugin_dir)),
        patch("cli.os.path.islink", return_value=False),
        patch("cli.os.symlink", side_effect=FileExistsError),
    ):
        args = argparse.Namespace(hermes_home=str(hermes_plugins.parent))
        cmd_link(args)

    captured = capsys.readouterr()
    assert "already" in captured.out.lower()


def test_cmd_update_registers_update_subcommand() -> None:
    from cli import register_cli

    parser = argparse.ArgumentParser(prog="ai-memory")
    register_cli(parser)
    args = parser.parse_args(["update"])
    assert args.ai_memory_command == "update"


def test_cmd_update_downloads_nothing(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """The self-update path executed unreviewed branch-head code inside Hermes.

    It was removed. `update` now only prints the pinned reinstall command.
    """
    import cli

    args = argparse.Namespace(hermes_home=str(tmp_path / ".hermes"))
    cli.cmd_update(args)

    captured = capsys.readouterr()
    assert "does not download code" in captured.out
    assert "hermes plugins install" in captured.out
    assert "--ref" in captured.out
    # The module must not carry a downloader any more.
    assert not hasattr(cli, "urllib")
    assert not hasattr(cli, "zipfile")
    assert not hasattr(cli, "REPO_TARBALL_URL")


def test_cmd_update_reports_missing_install(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from cli import cmd_update

    cmd_update(argparse.Namespace(hermes_home=str(tmp_path / ".hermes")))
    assert "(not installed)" in capsys.readouterr().out


def test_cmd_update_touches_nothing_on_disk(tmp_path: Path) -> None:
    from cli import cmd_update

    hermes_home = tmp_path / ".hermes"
    plugin_dir = hermes_home / "plugins" / "ai-memory"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("# installed")

    cmd_update(argparse.Namespace(hermes_home=str(hermes_home)))

    assert (plugin_dir / "__init__.py").read_text() == "# installed"
    assert not (hermes_home / ".ai-memory-backups").exists()


def test_ai_memory_command_defaults_to_status(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from cli import ai_memory_command

    mock_client = MagicMock()
    mock_client.status.return_value = {"counts": {"pages_latest": 1, "sessions": 2}}
    with patch("cli.AiMemoryClient", return_value=mock_client):
        ai_memory_command(argparse.Namespace(hermes_home=str(tmp_path)))

    assert "reachable" in capsys.readouterr().out


def test_ai_memory_command_reports_an_unknown_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cli import ai_memory_command

    ai_memory_command(argparse.Namespace(ai_memory_command="bogus"))
    out = capsys.readouterr().out
    assert "Unknown ai-memory command: bogus" in out
    assert "status" in out


def test_ai_memory_command_dispatches_to_the_subcommand(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from cli import ai_memory_command

    ai_memory_command(argparse.Namespace(ai_memory_command="update", hermes_home=str(tmp_path)))
    assert "does not download code" in capsys.readouterr().out


def test_hermes_home_falls_back_to_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hermes never sets args.hermes_home, so every subcommand used to raise
    AttributeError once the parser was fixed."""
    import cli

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "hermes_constants", None)
    assert cli._hermes_home(argparse.Namespace()) == str(tmp_path)


def test_hermes_home_prefers_an_explicit_attribute(tmp_path: Path) -> None:
    import cli

    assert cli._hermes_home(argparse.Namespace(hermes_home=str(tmp_path))) == str(tmp_path)


def test_cmd_link_reports_a_non_symlink_target(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from cli import cmd_link

    (tmp_path / "plugins" / "ai-memory").mkdir(parents=True)
    cmd_link(argparse.Namespace(hermes_home=str(tmp_path)))
    assert "not a symlink" in capsys.readouterr().out


def test_cmd_config_set_reports_skipped_secrets(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cli import cmd_config_set

    monkeypatch.setattr("cli.save_config", lambda values, home: ["auth_token"])
    cmd_config_set(argparse.Namespace(hermes_home=str(tmp_path), key="workspace", value="ws"))
    assert "skipped: auth_token" in capsys.readouterr().out
