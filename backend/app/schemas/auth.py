"""Request and response shapes for the auth routes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    # Bounds only. Composition rules are deliberately absent (NIST 800-63B);
    # the substantive check lives in services.auth.password_problem.
    password: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class DeleteAccountRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)


class UpdateMeRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    memory_enabled: bool | None = None


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str | None
    created_at: datetime
    memory_enabled: bool

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    """The access token is returned in the body, never set as a cookie.

    It is held in memory by the client and dies with the tab; the refresh
    token is the HttpOnly cookie, and is the only thing that survives a reload.
    """

    access_token: str
    expires_in: int
    user: UserOut


class WsTicketOut(BaseModel):
    ticket: str
    expires_in: int
