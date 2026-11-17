import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-long-enough-for-jwt-signing")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test-secret-key")
os.environ.setdefault("AWS_STORAGE_BUCKET_NAME", "test-bucket")
os.environ.setdefault("AWS_S3_REGION_NAME", "us-east-1")

from .base import *  # noqa

SECRET_KEY = "test-only-secret-key-long-enough-for-jwt-signing"
DEBUG = False
ALLOWED_HOSTS = ["testserver"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
# Background jobs run inline on commit: no Temporal server in tests.
JOBS_RUN_INLINE = True
JWT_COOKIE_SECURE = False
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# The throttle cache is shared by the whole test process, so real rates would
# 429 whichever test happened to make the sixth login of the minute.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {scope: None for scope in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]},
}

# Tests never reach a real model provider: hermetic tiers and no API key.
AI_LLM_PROVIDER = "groq"
AI_MODEL_TIERS = {"light": "test-light", "research": "test-research", "answer": "test-answer"}
GROQ_API_KEY = ""

# Deterministic in-process embeddings: no model download in tests.
KNOWLEDGE_EMBEDDING_PROVIDER = "hashing"
KNOWLEDGE_DOSSIER_DELAY_SECONDS = 0
DISCOVERY_RUNNER = "fake"
DISCOVERY_FAKE_STEP_SECONDS = 0
TAVILY_API_KEY = "test-tavily-key"
LANGSMITH_TRACING = False
PROPOSAL_RUNNER = "fake"
PROPOSAL_FAKE_STEP_SECONDS = 0
HUNTER_API_KEY = ""
# Tests never depend on the developer's optional keys: no fallback model, no renderer
# or speed-check keys unless a test sets them.
AI_FALLBACK_PROVIDER = ""
GEMINI_API_KEY = ""
JINA_API_KEY = ""
FIRECRAWL_API_KEY = ""
PAGESPEED_API_KEY = ""
