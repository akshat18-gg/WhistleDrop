import io

import pytest
from sqlalchemy import select

from app import cli
from app.models import Moderator


def run_cli(monkeypatch, *args, stdin=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    cli.main(list(args))


def test_create_moderator_from_stdin_and_log_in(monkeypatch, session, client, capsys):
    run_cli(monkeypatch, "create-moderator", "alice", "--password-stdin", stdin="a-long-password\n")
    assert "Created moderator 'alice'" in capsys.readouterr().out

    moderator = session.scalar(select(Moderator).where(Moderator.username == "alice"))
    assert moderator.is_active
    assert moderator.password_hash.startswith("$argon2id$")
    assert "a-long-password" not in moderator.password_hash

    response = client.post("/api/auth/login", json={"username": "alice", "password": "a-long-password"})
    assert response.status_code == 200


def test_create_moderator_prompts_twice_without_stdin_flag(monkeypatch, session):
    answers = iter(["typed-password-1", "typed-password-1"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))
    cli.main(["create-moderator", "bob"])
    assert session.scalar(select(Moderator).where(Moderator.username == "bob"))


def test_mismatched_prompted_passwords_are_refused(monkeypatch, session):
    answers = iter(["typed-password-1", "typed-password-2"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))
    with pytest.raises(SystemExit, match="don't match"):
        cli.main(["create-moderator", "bob"])
    assert session.scalar(select(Moderator)) is None


def test_short_password_is_refused(monkeypatch, session):
    with pytest.raises(SystemExit, match="at least 10"):
        run_cli(monkeypatch, "create-moderator", "alice", "--password-stdin", stdin="short\n")
    assert session.scalar(select(Moderator)) is None


@pytest.mark.parametrize("username", ["ab", "has space", "x" * 33, "semi;colon"])
def test_bad_usernames_are_refused(monkeypatch, username):
    with pytest.raises(SystemExit, match="Usernames"):
        run_cli(monkeypatch, "create-moderator", username, "--password-stdin", stdin="a-long-password\n")


def test_duplicate_username_is_refused(monkeypatch):
    run_cli(monkeypatch, "create-moderator", "alice", "--password-stdin", stdin="a-long-password\n")
    with pytest.raises(SystemExit, match="already exists"):
        run_cli(monkeypatch, "create-moderator", "alice", "--password-stdin", stdin="another-password\n")


def test_deactivate_moderator_blocks_login(monkeypatch, client):
    run_cli(monkeypatch, "create-moderator", "alice", "--password-stdin", stdin="a-long-password\n")
    run_cli(monkeypatch, "deactivate-moderator", "alice")
    response = client.post("/api/auth/login", json={"username": "alice", "password": "a-long-password"})
    assert response.status_code == 401


def test_deactivating_unknown_moderator_fails(monkeypatch):
    with pytest.raises(SystemExit, match="no moderator"):
        run_cli(monkeypatch, "deactivate-moderator", "ghost")
