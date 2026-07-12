"""
Policy engine — thin client over OPA (Open Policy Agent).

Policy lives in Rego, versioned and tested independently of application
code, and is evaluated before planning/execution starts (see the
level-1 system-context diagram: control plane sits *before* the data
plane in the request path).
"""
from __future__ import annotations

import logging

import httpx

from cdwia.common.config import settings
from cdwia.common.models import PolicyDecision, Principal

logger = logging.getLogger("cdwia.policy")


class PolicyEngine:
    def __init__(self, opa_url: str | None = None, client: httpx.Client | None = None):
        self.opa_url = opa_url or settings.opa_url
        self.client = client or httpx.Client(timeout=2.0)

    def check(self, principal: Principal, action: str, resource: str | None = None) -> PolicyDecision:
        payload = {
            "input": {
                "user_id": principal.user_id,
                "tenant_id": principal.tenant_id,
                "business_unit": principal.business_unit,
                "roles": principal.roles,
                "attributes": principal.attributes,
                "action": action,
                "resource": resource,
            }
        }
        try:
            resp = self.client.post(self.opa_url, json=payload)
            resp.raise_for_status()
            body = resp.json()
            allowed = bool(body.get("result", False))
            return PolicyDecision(
                allowed=allowed,
                reason="opa_decision" if allowed else "denied_by_policy",
            )
        except httpx.HTTPError as e:
            logger.error("OPA policy check failed (%s); failing closed", e)
            return PolicyDecision(allowed=False, reason=f"policy_engine_unreachable: {e}")
