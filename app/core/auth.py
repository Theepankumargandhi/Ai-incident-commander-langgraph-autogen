from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings


@dataclass(slots=True)
class AuthContext:
    role: str
    subject: str


def _match_token(expected: str | None, token: str | None) -> bool:
    return bool(expected and token and secrets.compare_digest(expected, token))


def resolve_auth_context(settings: Settings, authorization: str | None) -> AuthContext:
    if not settings.auth_enabled:
        return AuthContext(role="operator", subject="demo-mode")

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()

    if _match_token(settings.operator_api_token, token):
        return AuthContext(role="operator", subject="operator-token")
    if _match_token(settings.viewer_api_token, token):
        return AuthContext(role="viewer", subject="viewer-token")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is required for this endpoint.",
    )


def require_viewer(settings: Settings):
    def dependency(authorization: str | None = Header(default=None)) -> AuthContext:
        return resolve_auth_context(settings, authorization)

    return dependency


def require_operator(settings: Settings):
    def dependency(authorization: str | None = Header(default=None)) -> AuthContext:
        context = resolve_auth_context(settings, authorization)
        if context.role != "operator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operator access is required for this endpoint.",
            )
        return context

    return dependency


def require_remediation_token(settings: Settings):
    def dependency(
        remediation_token: str | None = Header(default=None, alias="X-Remediation-Token"),
    ) -> AuthContext:
        if settings.remediation_token and not _match_token(settings.remediation_token, remediation_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid remediation token.",
            )
        return AuthContext(role="remediation-gateway", subject="remediation-token")

    return dependency
