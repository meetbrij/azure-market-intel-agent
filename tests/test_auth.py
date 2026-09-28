"""Entra ID auth with real signed JWTs: a locally generated RSA key stands in
for the tenant's signing key, so signature, issuer, audience, tenant and
expiry checks all run exactly as in production. No network."""

import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient

from app.api import auth
from app.config import Settings, get_settings
from app.jobs import store
from app.jobs.models import JobStatus

TENANT = "11111111-1111-1111-1111-111111111111"
API_ID = "22222222-2222-2222-2222-222222222222"
ALICE = "aaaaaaaa-0000-0000-0000-000000000001"  # analyst
BOB = "bbbbbbbb-0000-0000-0000-000000000002"  # approver
CAROL = "cccccccc-0000-0000-0000-000000000003"  # analyst

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    """PyJWKClient stand-in: resolves the token's kid to our public key."""

    def __init__(self) -> None:
        self.down = False

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        if self.down:
            raise jwt.PyJWKClientConnectionError("keys endpoint unreachable")
        if jwt.get_unverified_header(token).get("kid") != "k1":
            raise jwt.PyJWKClientError("Unable to find a signing key")
        return jwt.PyJWK.from_dict(
            {**jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True),
             "kid": "k1", "alg": "RS256"}
        )


def token(oid: str, roles: list[str], key: Any = KEY, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": auth.issuer(TENANT),
        "aud": API_ID,
        "tid": TENANT,
        "oid": oid,
        "name": oid[:8],
        "roles": roles,
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def bearer(oid: str, roles: list[str], **overrides: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(oid, roles, **overrides)}"}


@pytest.fixture
def jwks(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeJwks]:
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("AUTH_TENANT_ID", TENANT)
    monkeypatch.setenv("AUTH_API_CLIENT_ID", API_ID)
    get_settings.cache_clear()
    fake = FakeJwks()
    monkeypatch.setattr(auth, "jwks_client", lambda: fake)
    yield fake
    get_settings.cache_clear()


async def submit(client: AsyncClient, oid: str) -> str:
    resp = await client.post(
        "/api/v1/research",
        json={"query": "AWS operating income"},
        headers=bearer(oid, ["analyst"]),
    )
    assert resp.status_code == 202, resp.text
    job_id: str = resp.json()["job_id"]
    return job_id


async def pause(job_id: str) -> None:
    await store.update_job(
        job_id, status=JobStatus.AWAITING_APPROVAL, interrupt={"pass": 1}
    )


# ---------- token validation ----------


async def test_missing_token_is_401_with_challenge(
    client: AsyncClient, jwks: FakeJwks
) -> None:
    resp = await client.get("/api/v1/research")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "bad",
    [
        {"aud": "api://someone-else"},
        {"iss": "https://login.microsoftonline.com/other-tenant/v2.0"},
        {"tid": "other-tenant"},
        {"exp": int(time.time()) - 3600},  # expired (beyond the 60 s leeway)
    ],
    ids=["audience", "issuer", "tenant", "expired"],
)
async def test_invalid_claims_are_401(
    client: AsyncClient, jwks: FakeJwks, bad: dict[str, Any]
) -> None:
    resp = await client.get("/api/v1/research", headers=bearer(ALICE, ["analyst"], **bad))
    assert resp.status_code == 401


async def test_forged_signature_is_401(client: AsyncClient, jwks: FakeJwks) -> None:
    forged = token(ALICE, ["approver"], key=OTHER_KEY)  # right kid, wrong key
    resp = await client.get(
        "/api/v1/research", headers={"Authorization": f"Bearer {forged}"}
    )
    assert resp.status_code == 401


async def test_unsigned_token_is_401(client: AsyncClient, jwks: FakeJwks) -> None:
    unsigned = jwt.encode(
        {"oid": ALICE, "roles": ["approver"]}, "", algorithm="none"
    )
    resp = await client.get(
        "/api/v1/research", headers={"Authorization": f"Bearer {unsigned}"}
    )
    assert resp.status_code == 401


async def test_signing_keys_outage_is_503_not_401(
    client: AsyncClient, jwks: FakeJwks
) -> None:
    jwks.down = True
    resp = await client.get("/api/v1/research", headers=bearer(ALICE, ["analyst"]))
    assert resp.status_code == 503


async def test_me_reports_identity_and_roles(
    client: AsyncClient, jwks: FakeJwks
) -> None:
    resp = await client.get("/api/v1/me", headers=bearer(BOB, ["approver"]))
    assert resp.json() == {"oid": BOB, "name": BOB[:8], "roles": ["approver"]}


async def test_health_stays_open_for_probes(
    client: AsyncClient, jwks: FakeJwks, queue: AsyncMock
) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200


# ---------- roles and visibility ----------


async def test_no_role_cannot_submit(client: AsyncClient, jwks: FakeJwks) -> None:
    resp = await client.post(
        "/api/v1/research", json={"query": "AWS"}, headers=bearer(ALICE, [])
    )
    assert resp.status_code == 403


async def test_submission_records_submitter_and_audit_event(
    client: AsyncClient, jwks: FakeJwks
) -> None:
    job_id = await submit(client, ALICE)

    job = await store.get_job(job_id)
    assert job is not None and job.submitted_by == ALICE
    [event] = await store.list_audit(job_id)
    assert (event.action, event.actor) == ("job_submitted", ALICE)
    assert event.detail["query"] == "AWS operating income"


async def test_analysts_see_only_their_own_jobs(
    client: AsyncClient, jwks: FakeJwks
) -> None:
    alices = await submit(client, ALICE)
    carols = await submit(client, CAROL)
    as_alice = bearer(ALICE, ["analyst"])

    listed = (await client.get("/api/v1/research", headers=as_alice)).json()
    assert [j["job_id"] for j in listed] == [alices]
    assert (await client.get(f"/api/v1/research/{carols}", headers=as_alice)).status_code == 404
    report = await client.get(f"/api/v1/research/{carols}/report.md", headers=as_alice)
    assert report.status_code == 404  # not 403: ids can't be probed

    as_bob = bearer(BOB, ["approver"])
    everyone = (await client.get("/api/v1/research", headers=as_bob)).json()
    assert {j["job_id"] for j in everyone} == {alices, carols}


async def test_analyst_cannot_approve_or_see_operations(
    client: AsyncClient, jwks: FakeJwks
) -> None:
    job_id = await submit(client, ALICE)
    await pause(job_id)
    as_carol = bearer(CAROL, ["analyst"])

    resume = await client.post(
        f"/api/v1/research/{job_id}/resume", json={"approved": True}, headers=as_carol
    )
    ops = await client.get("/api/v1/ops/status", headers=as_carol)

    assert (resume.status_code, ops.status_code) == (403, 403)


# ---------- separation of duties ----------


async def test_approver_cannot_approve_own_job(
    client: AsyncClient, jwks: FakeJwks, queue: AsyncMock
) -> None:
    job_id = await submit(client, BOB)  # an approver may also submit
    await pause(job_id)
    queue.reset_mock()

    resp = await client.post(
        f"/api/v1/research/{job_id}/resume",
        json={"approved": True},
        headers=bearer(BOB, ["approver"]),
    )

    assert resp.status_code == 403
    queue.enqueue_job.assert_not_awaited()
    job = await store.get_job(job_id)
    assert job is not None and job.status == JobStatus.AWAITING_APPROVAL
    actions = [e.action for e in await store.list_audit(job_id)]
    assert actions == ["job_submitted", "approval_refused"]


async def test_second_person_approves_and_is_recorded(
    client: AsyncClient, jwks: FakeJwks, queue: AsyncMock
) -> None:
    job_id = await submit(client, ALICE)
    await pause(job_id)
    queue.reset_mock()

    resp = await client.post(
        f"/api/v1/research/{job_id}/resume",
        json={"approved": False, "notes": "Narrow to AWS"},
        headers=bearer(BOB, ["approver"]),
    )

    assert resp.status_code == 202
    decision = queue.enqueue_job.await_args.kwargs["resume"]
    assert decision["reviewer"] == {"oid": BOB, "name": BOB[:8]}
    events = await store.list_audit(job_id)
    assert [(e.action, e.actor) for e in events] == [
        ("job_submitted", ALICE),
        ("approval_decided", BOB),
    ]
    assert events[1].detail == {"approved": False, "notes": "Narrow to AWS", "pass": 1}


async def test_failed_audit_write_blocks_the_decision(
    client: AsyncClient,
    jwks: FakeJwks,
    queue: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = await submit(client, ALICE)
    await pause(job_id)
    queue.reset_mock()
    monkeypatch.setattr(
        store, "record_audit", AsyncMock(side_effect=ConnectionError("db down"))
    )

    resp = await client.post(
        f"/api/v1/research/{job_id}/resume",
        json={"approved": True},
        headers=bearer(BOB, ["approver"]),
    )

    assert resp.status_code == 503
    queue.enqueue_job.assert_not_awaited()
    job = await store.get_job(job_id)
    assert job is not None and job.status == JobStatus.AWAITING_APPROVAL


# ---------- startup guard ----------


def settings(**overrides: Any) -> Settings:
    return get_settings().model_copy(update=overrides)


def test_bypass_is_refused_outside_local() -> None:
    with pytest.raises(RuntimeError, match="only allowed when ENVIRONMENT=local"):
        auth.check_auth_config(settings(dev_auth_bypass=True, environment="prod"))
    auth.check_auth_config(settings(dev_auth_bypass=True, environment="local"))


def test_real_mode_needs_tenant_and_audience() -> None:
    with pytest.raises(RuntimeError, match="AUTH_TENANT_ID"):
        auth.check_auth_config(
            settings(dev_auth_bypass=False, auth_tenant_id=None, auth_api_client_id=None)
        )
    auth.check_auth_config(
        settings(dev_auth_bypass=False, auth_tenant_id=TENANT, auth_api_client_id=API_ID)
    )
