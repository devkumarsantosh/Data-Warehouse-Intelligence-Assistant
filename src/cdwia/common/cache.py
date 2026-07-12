"""
Result cache. Keyed on normalized query text + the requester's data
scope (tenant/business unit), so cached answers are safe to reuse
across users within the same scope but never leak across it.
"""
from __future__ import annotations

import hashlib
import json
import re

import redis.asyncio as redis

from cdwia.common.config import settings
from cdwia.common.models import Principal, SynthesizedAnswer

_WHITESPACE = re.compile(r"\s+")


def normalize_query(text: str) -> str:
    return _WHITESPACE.sub(" ", text.strip().lower())


def cache_key(query_text: str, principal: Principal) -> str:
    normalized = normalize_query(query_text)
    scope = f"{principal.tenant_id}:{principal.business_unit}"
    digest = hashlib.sha256(f"{scope}|{normalized}".encode()).hexdigest()
    return f"cdwia:answer:{digest}"


class ResultCache:
    def __init__(self, redis_url: str | None = None, ttl_seconds: int = 900):
        self._client = redis.from_url(redis_url or settings.redis_url, decode_responses=True)
        self.ttl_seconds = ttl_seconds

    async def get(self, query_text: str, principal: Principal) -> SynthesizedAnswer | None:
        raw = await self._client.get(cache_key(query_text, principal))
        if raw is None:
            return None
        return SynthesizedAnswer.model_validate(json.loads(raw))

    async def set(self, query_text: str, principal: Principal, answer: SynthesizedAnswer) -> None:
        await self._client.set(
            cache_key(query_text, principal),
            answer.model_dump_json(),
            ex=self.ttl_seconds,
        )
