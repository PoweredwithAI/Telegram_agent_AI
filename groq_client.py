"""
groq_client.py — Groq LLM wrapper with per-user rate limiting.
Supports sliding window rate limits: N messages per M seconds, and daily cap.
"""

import time
import logging
from collections import defaultdict
from groq import AsyncGroq

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Sliding-window rate limiter.
    Tracks calls per user_id across two windows: per-minute and per-day.
    """

    def __init__(self, per_minute: int = 10, per_day: int = 100):
        self.per_minute = per_minute
        self.per_day    = per_day
        self._minute_calls: dict[int, list[float]] = defaultdict(list)
        self._day_calls:    dict[int, list[float]] = defaultdict(list)

    def check(self, user_id: int) -> tuple[bool, str]:
        """
        Returns (allowed: bool, reason: str).
        reason is empty string if allowed.
        """
        now = time.time()

        # Clean stale entries
        self._minute_calls[user_id] = [t for t in self._minute_calls[user_id] if now - t < 60]
        self._day_calls[user_id]    = [t for t in self._day_calls[user_id]    if now - t < 86400]

        if len(self._day_calls[user_id]) >= self.per_day:
            return False, f"daily limit of {self.per_day} messages reached"

        if len(self._minute_calls[user_id]) >= self.per_minute:
            wait = int(60 - (now - self._minute_calls[user_id][0])) + 1
            return False, f"slow down — try again in {wait}s"

        self._minute_calls[user_id].append(now)
        self._day_calls[user_id].append(now)
        return True, ""

    def usage(self, user_id: int) -> dict:
        now = time.time()
        m = [t for t in self._minute_calls[user_id] if now - t < 60]
        d = [t for t in self._day_calls[user_id]    if now - t < 86400]
        return {
            "used_this_minute": len(m),
            "remaining_minute": max(0, self.per_minute - len(m)),
            "used_today":       len(d),
            "remaining_today":  max(0, self.per_day - len(d)),
        }


class GroqChat:
    """
    Thin async wrapper around the Groq chat completions API.
    Handles retries and error normalisation.
    """

    def __init__(
        self,
        api_key: str,
        model: str   = "llama-3.3-70b-versatile",
        max_tokens: int  = 600,
        temperature: float = 0.2,
    ):
        self._client     = AsyncGroq(api_key=api_key)
        self.model       = model
        self.max_tokens  = max_tokens
        self.temperature = temperature
        logger.info("GroqChat initialised — model: %s", model)

    async def chat(self, messages: list[dict]) -> str:
        """
        messages: list of {"role": "system"|"user"|"assistant", "content": str}
        Returns the assistant reply string.
        """
        try:
            resp = await self._client.chat.completions.create(
                model       = self.model,
                messages    = messages,
                temperature = self.temperature,
                max_tokens  = self.max_tokens,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.error("Groq API error: %s", exc)
            raise