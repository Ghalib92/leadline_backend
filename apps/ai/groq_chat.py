"""ChatGroq that names Groq's "request too large" refusal (HTTP 413).

Groq's free tier caps a single request at the model's tokens-per-minute limit (8,000
for gpt-oss-120b). Above it Groq answers 413, which the SDK raises as a plain
APIStatusError, like any bad request. Raising RequestTooLarge instead lets the model's
fallback (Gemini) take exactly these calls (apps.ai.llm), while other bad requests are
still retried on the same model.
"""

import groq
from langchain_groq import ChatGroq

from apps.ai.exceptions import RequestTooLarge


def _named(exc: groq.APIStatusError) -> Exception:
    if exc.status_code == 413:
        return RequestTooLarge(str(exc))
    return exc


class LeadlineChatGroq(ChatGroq):
    def _generate(self, *args, **kwargs):
        try:
            return super()._generate(*args, **kwargs)
        except groq.APIStatusError as exc:
            named = _named(exc)
            if named is exc:
                raise
            raise named from exc

    async def _agenerate(self, *args, **kwargs):
        try:
            return await super()._agenerate(*args, **kwargs)
        except groq.APIStatusError as exc:
            named = _named(exc)
            if named is exc:
                raise
            raise named from exc
