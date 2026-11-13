class AIConfigError(Exception):
    """The AI layer is misconfigured: unknown role or tier, missing model or API key."""


class PromptError(Exception):
    """A prompt file is missing, or was rendered with the wrong variables."""


class StructuredOutputError(Exception):
    """The model did not produce output matching the schema, even after a retry."""

    def __init__(self, message, *, usage=None):
        super().__init__(message)
        self.usage = usage or {}
