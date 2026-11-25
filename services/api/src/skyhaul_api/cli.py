"""``flask spec`` (export or check the OpenAPI document) and ``flask skyhaul bootstrap``
(create the first admin; the password comes from stdin, never from an argument)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from flask import Flask, current_app
from flask.cli import AppGroup, with_appcontext
from marshmallow import ValidationError
from skyhaul_fleet_core.models import Role, User
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from skyhaul_api.audit.service import record
from skyhaul_api.auth.passwords import hash_password
from skyhaul_api.database import SESSIONS_KEY
from skyhaul_api.openapi import export_spec, spec_differences
from skyhaul_api.users.schemas import UserCreateSchema

DEFAULT_CONTRACT = Path("contracts/openapi/fleet-api.v1.json")


@click.command("spec")
@click.option(
    "--contract",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=DEFAULT_CONTRACT,
    show_default=True,
    help="The committed contract; its components fill the ones the app does not generate.",
)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), help="Write here.")
@click.option("--check", is_flag=True, help="Exit 1 if the spec differs from the contract.")
@with_appcontext
def spec_command(contract: Path, output: Path | None, check: bool) -> None:
    """Export the OpenAPI spec of the routes implemented so far."""
    committed = json.loads(contract.read_text())
    exported = export_spec(current_app, committed)
    if check:
        problems = spec_differences(exported, committed, current_app)
        for problem in problems:
            click.echo(problem, err=True)
        if problems:
            click.echo(f"OpenAPI drift: the app and {contract} differ.", err=True)
            sys.exit(1)
        click.echo(f"OpenAPI: the app matches {contract} for every implemented path.")
        return
    text = json.dumps(exported, indent=2) + "\n"
    if output is None:
        click.echo(text, nl=False)
    else:
        output.write_text(text)


skyhaul_cli = AppGroup("skyhaul", help="Skyhaul administration.")


@skyhaul_cli.command("bootstrap")
@click.option("--admin-email", required=True, help="Email of the first admin.")
def bootstrap_command(admin_email: str) -> None:
    """Create the first admin. Reads the password from stdin. Fails if the email exists."""
    password = sys.stdin.readline().rstrip("\r\n")
    try:
        body = UserCreateSchema().load(
            {"email": admin_email, "password": password, "role": Role.ADMIN.value}
        )
    except ValidationError as exc:
        fields = ", ".join(sorted(exc.messages_dict))
        raise click.ClickException(
            f"invalid {fields} (the password needs 12 to 1024 characters)"
        ) from None
    sessions: sessionmaker[Session] | None = current_app.extensions.get(SESSIONS_KEY)
    if sessions is None:
        raise click.ClickException("this app has no database; use skyhaul_api:create_app")
    with sessions() as session:
        if session.scalar(select(User.id).where(User.email == body["email"])) is not None:
            raise click.ClickException("a user with this email exists; nothing was created")
        user = User(email=body["email"], password_hash=hash_password(password), role=Role.ADMIN)
        session.add(user)
        session.flush()
        record(
            session,
            action="user.bootstrap",
            outcome="success",
            actor=None,
            target_type="user",
            target_id=str(user.id),
        )
        session.commit()
    click.echo("admin created")


def init_cli(app: Flask) -> None:
    app.cli.add_command(spec_command)
    app.cli.add_command(skyhaul_cli)
