"""Entra ID bearer tokens and app roles.

Every /api/v1 route requires a v2 access token issued by our tenant for the
API app registration. The signature is checked against the tenant's JWKS, plus
issuer, audience, tenant and expiry. The token's `roles` claim (Entra app
roles) decides what the caller may do:

    analyst   submit research; see own jobs
    approver  everything an analyst can, plus see all jobs and approve/reject

DEV_AUTH_BYPASS (local only) skips validation for offline work and tests.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

ANALYST = "analyst"
APPROVER = "approver"
DEV_USER_HEADER = "X-Dev-User"
DEV_ROLES_HEADER = "X-Dev-Roles"
_CLOCK_SKEW_S = 60


@dataclass(frozen=True)
class Principal:
    oid: str  # Entra object id: stable, unlike names or emails
    name: str
    roles: frozenset[str]

    def has(self, role: str) -> bool:
        # An approver can do everything an analyst can.
        return role in self.roles or (role == ANALYST and APPROVER in self.roles)

    @property
    def is_approver(self) -> bool:
        return self.has(APPROVER)


def check_auth_config(s: Settings) -> None:
    """Called at API startup: a bypass that can reach production is worse
    than no bypass, and a real mode without its settings can't work."""
    if s.dev_auth_bypass:
        if s.environment != "local":
            raise RuntimeError(
                f"DEV_AUTH_BYPASS=true is only allowed when ENVIRONMENT=local "
                f"(got {s.environment!r})"
            )
        log.warning("DEV_AUTH_BYPASS is on: tokens are NOT validated (local only)")
    elif not (s.auth_tenant_id and s.auth_api_client_id):
        raise RuntimeError(
            "AUTH_TENANT_ID and AUTH_API_CLIENT_ID are required "
            "(or DEV_AUTH_BYPASS=true with ENVIRONMENT=local)"
        )


def issuer(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/v2.0"


@lru_cache
def jwks_client() -> jwt.PyJWKClient:
    """The tenant's signing keys, cached (Entra rotates them; an unknown kid
    triggers a refetch)."""
    tenant = get_settings().auth_tenant_id
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        cache_keys=True,
        lifespan=3600,
    )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def verify_token(token: str) -> Principal:
    s = get_settings()
    assert s.auth_tenant_id and s.auth_api_client_id  # check_auth_config
    try:
        # Fetching keys is blocking I/O (only on a cache miss).
        key = await asyncio.to_thread(jwks_client().get_signing_key_from_jwt, token)
    except jwt.PyJWKClientConnectionError as e:
        log.exception("Could not fetch the tenant's signing keys")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Token keys unavailable"
        ) from e
    except jwt.PyJWTError as e:
        raise _unauthorized("Invalid token") from e
    try:
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            # v2 tokens carry the client id; accept the URI form too.
            audience=[s.auth_api_client_id, f"api://{s.auth_api_client_id}"],
            issuer=issuer(s.auth_tenant_id),
            leeway=_CLOCK_SKEW_S,
            options={"require": ["exp", "iat", "iss", "aud", "oid", "tid"]},
        )
    except jwt.PyJWTError as e:
        log.info("Rejected token: %s", e)
        raise _unauthorized("Invalid token") from e
    if claims["tid"] != s.auth_tenant_id:
        raise _unauthorized("Invalid token")
    return Principal(
        oid=claims["oid"],
        name=claims.get("name") or claims.get("preferred_username") or claims["oid"],
        roles=frozenset(claims.get("roles", [])),
    )


def _dev_principal(request: Request) -> Principal:
    user = request.headers.get(DEV_USER_HEADER, "dev-user")
    roles = request.headers.get(DEV_ROLES_HEADER, f"{ANALYST},{APPROVER}")
    return Principal(
        oid=f"dev:{user}",
        name=user,
        roles=frozenset(r.strip() for r in roles.split(",") if r.strip()),
    )


_bearer = HTTPBearer(auto_error=False)


async def current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if get_settings().dev_auth_bypass:
        return _dev_principal(request)
    if credentials is None:
        raise _unauthorized("Bearer token required")
    return await verify_token(credentials.credentials)


def require(role: str) -> Callable[[Principal], Awaitable[Principal]]:
    async def dependency(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        if not principal.has(role):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Requires the {role!r} app role"
            )
        return principal

    return dependency


Analyst = Annotated[Principal, Depends(require(ANALYST))]
Approver = Annotated[Principal, Depends(require(APPROVER))]
Authenticated = Annotated[Principal, Depends(current_principal)]
