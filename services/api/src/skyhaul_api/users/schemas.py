"""Input schemas (the contract's LoginRequest, UserCreate, UserUpdate) and the User output."""

from __future__ import annotations

from typing import Any

from marshmallow import RAISE, Schema, ValidationError, fields, validate, validates_schema
from skyhaul_fleet_core.models import Role, User

EMAIL_MAX = 254
PASSWORD_MAX = 1024
NEW_PASSWORD_MIN = 12
ROLES = [role.value for role in Role]


class LoginSchema(Schema):
    class Meta:
        unknown = RAISE

    email = fields.Email(required=True, validate=validate.Length(max=EMAIL_MAX))
    password = fields.String(required=True, validate=validate.Length(min=1, max=PASSWORD_MAX))


class UserCreateSchema(Schema):
    class Meta:
        unknown = RAISE

    email = fields.Email(required=True, validate=validate.Length(max=EMAIL_MAX))
    password = fields.String(
        required=True, validate=validate.Length(min=NEW_PASSWORD_MIN, max=PASSWORD_MAX)
    )
    role = fields.String(required=True, validate=validate.OneOf(ROLES))


class UserUpdateSchema(Schema):
    class Meta:
        unknown = RAISE

    role = fields.String(validate=validate.OneOf(ROLES))
    is_active = fields.Boolean(truthy={True}, falsy={False})

    @validates_schema
    def _not_empty(self, data: dict[str, Any], **_kwargs: Any) -> None:
        if not data:
            raise ValidationError("Change at least one field.")


def user_json(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
    }
