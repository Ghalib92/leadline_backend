"""ChatGroq that names Groq's "request too large" refusal (HTTP 413) and rests a key
that hit its rate limit.

Groq's free tier caps a single request at the model's tokens-per-minute limit (8,000
for gpt-oss-120b). Above it Groq answers 413, which the SDK raises as a plain
APIStatusError, like any bad request. Raising RequestTooLarge instead lets the model's
fallback (Gemini) take exactly these calls (apps.ai.llm), while other bad requests are
still retried on the same model.

A rate-limited key is rested for as long as Groq's retry-after says (apps.ai.cooldown),
so while another option is left the next calls go straight to it.
"""

import groq
from langchain_groq import ChatGroq

from apps.ai import cooldown
from apps.ai.exceptions import RequestTooLarge


def _named(exc: groq.APIStatusError) -> Exception:
    if exc.status_code == 413:
        return RequestTooLarge(str(exc))
    return exc


def _retry_after(exc: groq.RateLimitError) -> float | None:
    try:
        return float(exc.response.headers.get("retry-after", ""))
    except (AttributeError, TypeError, ValueError):
        return None


class LeadlineChatGroq(ChatGroq):
    key_name: str = "GROQ_API_KEY"  # for logs; never the key itself
    has_backup: bool = False  # a later option exists: rest this key when limited

    def _key(self) -> str:
        return self.groq_api_key.get_secret_value() if self.groq_api_key else ""

    def _before(self) -> None:
        if self.has_backup:
            cooldown.skip_if_cooling("groq", self._key(), self.model_name, self.key_name)

    def _refused(self, exc: groq.APIStatusError) -> Exception:
        if isinstance(exc, groq.RateLimitError):
            cooldown.cool("groq", self._key(), self.model_name, self.key_name, _retry_after(exc))
        return _named(exc)

    def _generate(self, *args, **kwargs):
        self._before()
        try:
            return super()._generate(*args, **kwargs)
        except groq.APIStatusError as exc:
            named = self._refused(exc)
            if named is exc:
                raise
            raise named from exc

    async def _agenerate(self, *args, **kwargs):
        self._before()
        try:
            return await super()._agenerate(*args, **kwargs)
        except groq.APIStatusError as exc:
            named = self._refused(exc)
            if named is exc:
                raise
            raise named from exc
