from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class QuestionOut(BaseModel):
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
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: Optional[str] = None
    auth_provider: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
