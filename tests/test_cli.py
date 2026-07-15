"""Tests for the headless omniterm-cli (no GUI, no live SSH)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from omniterm import cli
from omniterm.core import config


@pytest.fixture
def fake_sessions(monkeypatch):
    store = {"version": "1.0", "sessions": [
        {"id": "1", "name": "prod", "type": "ssh", "host": "h1", "user": "u1",
         "port": 22, "auth_method": "password", "password": "ENC"},
        {"id": "2", "name": "box", "type": "local"},
        {"id": "f", "type": "folder", "name": "grp", "children": [
            {"id": "3", "name": "dev", "type": "ssh", "host": "h3", "user": "u3",
             "port": 2222, "auth_method": "key", "key_path": "/k"}]},
    ]}
    monkeypatch.setattr(config, "load_sessions",
                        lambda: json.loads(json.dumps(store)))
    saved = {}
    monkeypatch.setattr(config, "save_sessions",
                        lambda data: saved.update(data=data))
    return store, saved


def test_iter_flattens_folders(fake_sessions):
    assert [s["name"] for s in cli.iter_sessions()] == ["prod", "box", "dev"]


def test_resolve_by_name_and_id(fake_sessions):
    assert cli.resolve_session("dev")["port"] == 2222
    assert cli.resolve_session("3")["name"] == "dev"
    assert cli.resolve_session("nope") is None


def test_session_list_json(fake_sessions, capsys):
    cli.main(["session", "list", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert {r["name"] for r in rows} == {"prod", "box", "dev"}


def test_session_show_redacts_password(fake_sessions, capsys):
    cli.main(["session", "show", "prod", "--json"])
    obj = json.loads(capsys.readouterr().out)
    assert "password" not in obj and obj["has_password"] is True


def test_session_add_and_remove(fake_sessions, monkeypatch):
    _store, saved = fake_sessions
    monkeypatch.setattr(config, "encrypt_password", lambda pw: f"ENC({pw})")
    rc = cli.main(["session", "add", "new", "--host", "h9", "--user", "u9",
                   "--auth", "password", "--password", "secret"])
    assert rc == 0
    added = [s for s in saved["data"]["sessions"] if s.get("name") == "new"][0]
    assert added["host"] == "h9" and added["password"] == "ENC(secret)"

    # removing prunes at any depth (also inside folders)
    cli.main(["session", "remove", "dev"])
    names = [s["name"] for s in cli.iter_sessions(saved["data"]["sessions"])]
    assert "dev" not in names


def test_session_add_rejects_duplicate(fake_sessions):
    assert cli.main(["session", "add", "prod", "--host", "x", "--user", "y"]) == 1


def test_exec_json_and_exit_code(fake_sessions, monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_exec",
                        lambda s, c, timeout=None: {"stdout": "hi\n", "stderr": "",
                                                    "exit_code": 0})
    rc = cli.main(["exec", "prod", "echo hi", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["stdout"] == "hi\n"

    monkeypatch.setattr(cli, "run_exec",
                        lambda s, c, timeout=None: {"stdout": "", "stderr": "boom\n",
                                                    "exit_code": 3})
    assert cli.main(["exec", "prod", "false", "--json"]) == 3


def test_exec_rejects_non_ssh_session(fake_sessions, capsys):
    assert cli.main(["exec", "box", "ls"]) == 1
    assert "not" in capsys.readouterr().err.lower()


def test_sftp_ls_json(fake_sessions, monkeypatch, capsys):
    monkeypatch.setattr(cli, "sftp_ls", lambda s, p: [
        {"name": "d", "size": 0, "is_dir": True, "mtime": 0},
        {"name": "f.txt", "size": 12, "is_dir": False, "mtime": 0}])
    cli.main(["sftp", "ls", "prod", "/tmp", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["is_dir"] and rows[1]["name"] == "f.txt"


def test_unknown_target_errors(fake_sessions, capsys):
    assert cli.main(["exec", "ghost", "ls"]) == 1


def test_no_command_prints_help(fake_sessions):
    assert cli.main([]) == 2


def test_cli_module_is_gui_free():
    # The CLI must not pull in PyQt (agents run it without a display). Checked in
    # a FRESH interpreter, since other tests in this process import PyQt6.
    import subprocess
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    code = ("import sys, omniterm.cli; "
            "assert 'PyQt6' not in sys.modules, 'CLI imported PyQt6'; print('ok')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=dict(os.environ, PYTHONPATH=src))
    assert r.returncode == 0, r.stderr
