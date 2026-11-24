"""``flask spec``: export the OpenAPI document, or check it against the contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from flask import Flask, current_app
from flask.cli import with_appcontext

from skyhaul_api.openapi import export_spec, spec_differences

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


def init_cli(app: Flask) -> None:
    app.cli.add_command(spec_command)
