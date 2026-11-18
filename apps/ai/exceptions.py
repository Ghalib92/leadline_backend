class AIConfigError(Exception):
    """The AI layer is misconfigured: unknown role or tier, missing model or API key."""


class PromptError(Exception):
    """A prompt file is missing, or was rendered with the wrong variables."""


class StructuredOutputError(Exception):
    """The model did not produce output matching the schema, even after a retry."""

    def __init__(self, message, *, usage=None):
        super().__init__(message)
        self.usage = usage or {}

    @property
    def rate_limited(self) -> bool:
        """The provider refused for its rate limit (HTTP 429), not a bad answer: trying
        again in the same run won't help."""
        cause = self.__cause__
        return getattr(cause, "status_code", None) == 429 or "RateLimit" in type(cause).__name__


class RequestTooLarge(Exception):
    """The provider refuses the request's size on the current plan (Groq's free tier
    caps one request at its tokens-per-minute limit). A fallback model with a larger
    allowance can answer it; retrying the same model can't."""
