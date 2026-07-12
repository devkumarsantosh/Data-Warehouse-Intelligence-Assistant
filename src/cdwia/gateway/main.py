"""
API gateway: the single entry point (Section 2, level-1 diagram).
Responsibilities kept here, and only here: AuthN (OIDC token
verification), coarse rate limiting, and WAF-style request sanitation.
Authorization (RBAC/ABAC) is a control-plane concern (policy.py), not
the gateway's — the gateway answers "who are you", not "what can you do".
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from cdwia.common.models import IncomingQuery, Principal

app = FastAPI(title="CDWIA Gateway", version="2.0.0")

# Naive in-memory token bucket per user; production uses Redis so limits
# are enforced consistently across replicas.
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_REQUESTS = 30
_request_log: dict[str, list[float]] = defaultdict(list)


class QueryRequest(BaseModel):
    text: str


def _verify_token(authorization: str | None) -> Principal:
    """Stand-in for real OIDC verification (Azure AD / Okta JWKS
    validation). Wire this to the actual identity provider in prod."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")
    # TODO: replace with real JWT verification + claims extraction.
    return Principal(
        user_id="demo-user",
        tenant_id="demo-tenant",
        business_unit="finops",
        roles=["analyst"],
    )


def _check_rate_limit(principal: Principal) -> None:
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW_SECONDS
    log = _request_log[principal.user_id]
    while log and log[0] < window_start:
        log.pop(0)
    if len(log) >= _RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    log.append(now)


def _reject_obvious_waf_signatures(text: str) -> None:
    """Placeholder WAF layer: reject obviously malicious payloads before
    they reach any downstream service. A real deployment fronts this
    with a managed WAF (e.g. AWS WAF/Cloudflare), this is a light
    application-level backstop, not a replacement for one."""
    suspicious_markers = ("<script", "DROP TABLE", "../../")
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in suspicious_markers):
        raise HTTPException(status_code=400, detail="Request rejected by WAF policy")


@app.post("/v1/query")
async def submit_query(body: QueryRequest, request: Request, authorization: str | None = Header(default=None)):
    principal = _verify_token(authorization)
    _check_rate_limit(principal)
    _reject_obvious_waf_signatures(body.text)

    query = IncomingQuery(
        request_id=str(uuid.uuid4()),
        principal=principal,
        text=body.text,
    )

    # In the real system this forwards to the control plane's planning
    # endpoint (classifier -> policy -> decision engine). Wired here as
    # a placeholder so the gateway is independently runnable/testable.
    from cdwia.control_plane.classifier import DeterministicClassifier

    classification = DeterministicClassifier().classify(query.text)
    return {
        "request_id": query.request_id,
        "classification": classification.model_dump(),
        "status": "accepted",
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
