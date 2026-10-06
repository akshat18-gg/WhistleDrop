import argparse
import getpass
import re
import sys

from sqlalchemy import select

from app.db import SessionLocal, create_tables
from app.models import Moderator
from app.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, hash_password

USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{3,32}")


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("The passwords don't match.")
    return password


def create_moderator(username: str, password: str) -> None:
    if not USERNAME_PATTERN.fullmatch(username):
        raise SystemExit("Usernames are 3 to 32 characters: letters, digits, dots, dashes and underscores.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"The password needs at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise SystemExit(f"The password can't be longer than {MAX_PASSWORD_LENGTH} characters.")

    with SessionLocal() as db:
        if db.scalar(select(Moderator).where(Moderator.username == username)):
            raise SystemExit(f"A moderator called '{username}' already exists.")
        db.add(Moderator(username=username, password_hash=hash_password(password)))
        db.commit()
    print(f"Created moderator '{username}'.")


def deactivate_moderator(username: str) -> None:
    with SessionLocal() as db:
        moderator = db.scalar(select(Moderator).where(Moderator.username == username))
        if moderator is None:
            raise SystemExit(f"There's no moderator called '{username}'.")
        moderator.is_active = False
        db.commit()
    print(f"Deactivated '{username}'. They can't log in, and tokens they already have stop working.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Manage WhistleDrop moderators.")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-moderator", help="add a moderator account")
    create.add_argument("username")
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from standard input instead of prompting (for scripts)",
    )

    deactivate = commands.add_parser("deactivate-moderator", help="stop a moderator from logging in")
    deactivate.add_argument("username")

    args = parser.parse_args(argv)
    create_tables()
    if args.command == "create-moderator":
        create_moderator(args.username, read_password(args.password_stdin))
    else:
        deactivate_moderator(args.username)


if __name__ == "__main__":
    main()
