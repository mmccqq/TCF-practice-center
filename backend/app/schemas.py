"""API schemas: what the HTTP layer accepts and returns.

These are Pydantic models, not database models - the SQLAlchemy tables live in
models.py. Keeping the two separate is what stops a column like
User.hashed_password from reaching a response: UserOut simply never declares it.

Pydantic earns its place by doing four jobs, all driven by the annotations below:

1. Validation - the type *is* the rule. `password: str = Field(min_length=8)`
   replaces a hand-written isinstance/len check, reports every bad field at once
   rather than the first, and FastAPI renders that as a 422 response.
2. Coercion - JSON bodies and query strings are effectively untyped, so `?tache=3`
   arrives as the string "3"; `tache: int` converts it before the endpoint runs.
   Only unambiguous conversions: "hello" raises instead of guessing.
3. Serialisation - responses are rendered from these classes, which is also where
   Python's None becomes JSON null.
4. Documentation - /docs is generated from these definitions, so it cannot drift
   out of sync with the code.

Naming: `...In` schemas are request bodies, `...Out` schemas are responses.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class QuestionOut(BaseModel):
    # built from a SQLAlchemy Question row, so Pydantic has to read attributes
    # (row.id) instead of its default dict lookup (row["id"]); without this the
    # conversion raises ValidationError on every request. Pydantic v1 spelled it
    # `class Config: orm_mode = True`.
    model_config = ConfigDict(from_attributes=True)

    id: str
    tache: int
    text: str
    source: str
    year: int
    month: int
    period: str
    partie: Optional[int] = None
    sujet: Optional[int] = None
    source_url: Optional[str] = None
    occurrences: int


class QuestionPage(BaseModel):
    """Paginated envelope - the bank is a few thousand rows, so the list
    endpoints never return everything at once."""

    items: List[QuestionOut]
    total: int
    page: int
    per_page: int
    pages: int


class SignUpIn(BaseModel):
    # no from_attributes here: the In schemas are built from request JSON, which
    # is already a dict, and never from an ORM row.
    # EmailStr checks the address is well-formed (needs the email-validator package);
    # Field() attaches constraints that a bare `str` annotation cannot express.
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    # as in QuestionOut: built from a User row. Note what is absent -
    # hashed_password and google_sub exist on the model but not here, so they
    # cannot leak into a response even by accident.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: Optional[str] = None
    auth_provider: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
