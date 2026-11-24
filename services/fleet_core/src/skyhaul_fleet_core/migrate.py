"""``skyhaul-migrate``: the Alembic command line, configured in code.

It takes the database URL from ``DATABASE_URL`` (never from a file or an argument, so the
password stays out of the process list) and the scripts from this package. Examples::

    skyhaul-migrate upgrade head
    skyhaul-migrate check
    skyhaul-migrate revision --autogenerate -m "add x"
"""

from __future__ import annotations

import sys
from importlib.resources import files

from alembic.config import CommandLine, Config

from skyhaul_fleet_core.settings import SettingsError, require


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(files("skyhaul_fleet_core") / "migrations"))
    # Passed as an attribute, not a main option, so it is never interpolated or printed.
    config.attributes["database_url"] = database_url
    return config


def main(argv: list[str] | None = None) -> None:
    try:
        database_url = require("DATABASE_URL")
    except SettingsError as exc:
        sys.exit(f"skyhaul-migrate: {exc}")
    cli = CommandLine(prog="skyhaul-migrate")
    options = cli.parser.parse_args(argv)
    if not hasattr(options, "cmd"):
        cli.parser.error("too few arguments")
    cli.run_cmd(alembic_config(database_url), options)


if __name__ == "__main__":
    main()
