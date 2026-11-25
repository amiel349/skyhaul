"""``flask spec`` (export or check the OpenAPI document), ``flask skyhaul bootstrap``
(create the first admin; the password comes from stdin, never from an argument) and
``flask skyhaul broker-init`` (broker users and topology; it reads only its own settings, so
it runs with the spec app: ``flask --app 'skyhaul_api:create_spec_app()'``)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
from flask import Flask, current_app
from flask.cli import AppGroup, with_appcontext
from marshmallow import ValidationError
from pika.exceptions import AMQPError
from skyhaul_fleet_core.models import Role, User
from skyhaul_fleet_core.settings import SettingsError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from skyhaul_api import broker_init
from skyhaul_api.audit.service import record
from skyhaul_api.auth.passwords import hash_password, verify_password
from skyhaul_api.database import SESSIONS_KEY
from skyhaul_api.openapi import export_spec, spec_differences
from skyhaul_api.users.schemas import NEW_PASSWORD_MIN, PASSWORD_MAX, UserCreateSchema

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


@skyhaul_cli.command("ensure-admin")
def ensure_admin_command() -> None:
    """Local development: make sure the admin in DEV_ADMIN_EMAIL / DEV_ADMIN_PASSWORD exists.

    Creates it, or syncs the password and role when `.env` changed. Does nothing when the
    password is empty. Never prints the password.

    Unlike `bootstrap` and `POST /users` it accepts a short password (like `1234`), so a local
    login is easy to type. That is only safe because this runs from the local `dev` compose
    profile. Never wire it into a deploy.
    """
    email = os.environ.get("DEV_ADMIN_EMAIL", "").strip()
    password = os.environ.get("DEV_ADMIN_PASSWORD", "")
    if not email or not password:
        click.echo("no dev admin configured (DEV_ADMIN_EMAIL / DEV_ADMIN_PASSWORD); skipped")
        return
    try:
        # The schema checks the email; a stand-in password of the minimum length passes its rule.
        body = UserCreateSchema().load(
            {"email": email, "password": "x" * NEW_PASSWORD_MIN, "role": Role.ADMIN.value}
        )
    except ValidationError:
        raise click.ClickException("DEV_ADMIN_EMAIL is not a valid email address") from None
    if len(password) > PASSWORD_MAX:
        raise click.ClickException(f"DEV_ADMIN_PASSWORD is longer than {PASSWORD_MAX} characters")
    sessions: sessionmaker[Session] | None = current_app.extensions.get(SESSIONS_KEY)
    if sessions is None:
        raise click.ClickException("this app has no database; use skyhaul_api:create_app")
    with sessions() as session:
        user = session.scalar(select(User).where(User.email == body["email"]))
        if user is None:
            user = User(email=body["email"], password_hash=hash_password(password), role=Role.ADMIN)
            session.add(user)
            outcome = "created"
        elif (
            verify_password(user.password_hash, password)
            and user.role == Role.ADMIN
            and user.is_active
        ):
            click.echo("dev admin is up to date")
            return
        else:
            user.password_hash = hash_password(password)
            user.role = Role.ADMIN
            user.is_active = True
            outcome = "updated"
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
    click.echo(f"dev admin {outcome}")


@skyhaul_cli.command("broker-init")
def broker_init_command() -> None:
    """Create the broker users and permissions and declare the topology (one-shot)."""
    try:
        broker_init.run(broker_init.load_config())
    except (SettingsError, broker_init.BrokerInitError) as exc:
        raise click.ClickException(str(exc)) from None
    except AMQPError as exc:
        raise click.ClickException(f"cannot declare the topology ({type(exc).__name__})") from None
    click.echo("broker users and topology ready")


def init_cli(app: Flask) -> None:
    app.cli.add_command(spec_command)
    app.cli.add_command(skyhaul_cli)
