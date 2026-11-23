"""Base class for every generated contract model (passed to datamodel-codegen as --base-class)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class ContractModel(BaseModel):
    """Frozen, tolerant reader, finite numbers only (ADR 0005).

    - ``extra="ignore"``: unknown payload fields are dropped, so additive changes need no
      version bump. The envelope overrides this with ``extra="forbid"`` from its schema.
    - ``frozen=True``: messages cross threads (consumer, outbox, publisher) and must not
      change after validation.
    - ``allow_inf_nan=False``: NaN and infinity are not JSON and are rejected on both sides.
    - Optional fields are never nullable (a contract test enforces this on the schemas).
      The generator types them ``X | None = None`` so they can be absent; an explicit
      ``null`` is rejected here, as the schema does. The Codec omits them when unset.
    """

    model_config = ConfigDict(extra="ignore", frozen=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _optional_fields_are_not_nullable(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for name, field in cls.model_fields.items():
                if not field.is_required() and name in data and data[name] is None:
                    raise ValueError(f"{name} may be absent but not null")
        return data
