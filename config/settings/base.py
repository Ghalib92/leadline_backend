import os
from datetime import timedelta
from pathlib import Path

from decouple import Csv, config

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parents[2]


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config("DJANGO_SECRET_KEY")


# Application definition

INSTALLED_APPS = [
    #My apps
    'apps.authentication',
    'apps.common',
    'apps.organizations',
    'apps.knowledge',
    'apps.discovery',
    'apps.opportunities',
    'apps.proposals',
    'apps.learning',
    'apps.outreach',
    'apps.ai',

    #Third party apps
    'corsheaders',
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'drf_spectacular',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

MIDDLEWARE = [
    "apps.common.middleware.ClientIPMiddleware",
    'django.middleware.security.SecurityMiddleware',
    "corsheaders.middleware.CorsMiddleware",
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = "config.asgi.application"

# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    "default": {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST', default="127.0.0.1"),
        'PORT': config('DB_PORT', default="5432"),
        "CONN_MAX_AGE": config("DB_CONN_MAX_AGE", cast=int, default=60),
        "CONN_HEALTH_CHECKS": True,
    }
}

db_sslmode = config("DB_SSLMODE", default="")
if db_sslmode:
    DATABASES["default"].setdefault("OPTIONS", {})["sslmode"] = db_sslmode

db_client_encoding = config("DB_CLIENT_ENCODING", default="")
if db_client_encoding:
    DATABASES["default"].setdefault("OPTIONS", {})["client_encoding"] = db_client_encoding


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

SPECTACULAR_SETTINGS = {
    "TITLE": "LEADLINE API",
    "DESCRIPTION": "API documentation for the LEADLINE backend platform.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "ENUM_NAME_OVERRIDES": {
        "OrganizationMemberStatusEnum": [
            ("active", "Active"), ("inactive", "Inactive"), ("removed", "Removed")
        ],
        "OrganizationInvitationStatusEnum": [
            ("pending", "Pending"), ("accepted", "Accepted"), ("declined", "Declined"),
            ("revoked", "Revoked"), ("expired", "Expired"),
        ],
        "OrganizationMemberWritableStatusEnum": ["active", "inactive"],
        "DiscoveryRunStatusEnum": "apps.discovery.choices.RunStatus",
        "ReviewDecisionEnum": "apps.opportunities.choices.ReviewDecision",
        "ProposalStatusEnum": "apps.proposals.choices.ProposalStatus",
        "CandidateDecisionEnum": "apps.discovery.choices.CandidateDecision",
        "ContactKindEnum": "apps.outreach.choices.ContactKind",
        "ContactSourceEnum": "apps.outreach.choices.ContactSource",
        "VersionSourceEnum": "apps.proposals.choices.VersionSource",
        "OpportunityStatusEnum": "apps.opportunities.choices.OpportunityStatus",
        "OpportunityTypeEnum": "apps.organizations.choices.OpportunityType",
        "RunStepStatusEnum": ["pending", "running", "done", "warning", "failed", "skipped"],
    },
}

AUTH_USER_MODEL = 'authentication.User'

# ModelBackend authenticates on USERNAME_FIELD (email); the manager normalises it.
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]

# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Africa/Nairobi'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "apps.common.api.pagination.StandardPagination",
    "PAGE_SIZE": config("API_PAGE_SIZE", cast=int, default=25),
    "DEFAULT_THROTTLE_RATES": {
        "login": config("LOGIN_THROTTLE_RATE", default="5/minute"),
        "registration": config("REGISTRATION_THROTTLE_RATE", default="5/hour"),
        # Verify/resend/forgot/reset. Each OTP is also burned after OTP_MAX_ATTEMPTS misses.
        "otp": config("OTP_THROTTLE_RATE", default="10/hour"),
        "organization_create": config("ORGANIZATION_CREATE_THROTTLE_RATE", default="10/hour"),
        "organization_invitation": config(
            "ORGANIZATION_INVITATION_THROTTLE_RATE", default="30/hour"
        ),
        "organization_invitation_token": config(
            "ORGANIZATION_INVITATION_TOKEN_THROTTLE_RATE", default="60/hour"
        ),
        "organization_document": config(
            "ORGANIZATION_DOCUMENT_THROTTLE_RATE", default="120/hour"
        ),
        # Reprocessing a document or regenerating the dossier costs model calls.
        "knowledge_refresh": config("KNOWLEDGE_REFRESH_THROTTLE_RATE", default="30/hour"),
        # Each run makes many model and search calls.
        "discovery_run": config("DISCOVERY_RUN_THROTTLE_RATE", default="20/hour"),
        # Each approval can start a Proposal Writer run.
        "opportunity_review": config("OPPORTUNITY_REVIEW_THROTTLE_RATE", default="300/hour"),
        # Full drafts and section rewrites make model calls.
        "proposal_job": config("PROPOSAL_JOB_THROTTLE_RATE", default="60/hour"),
        # Contact searches and AI email drafts make model calls.
        "outreach_job": config("OUTREACH_JOB_THROTTLE_RATE", default="60/hour"),
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # The reporting export endpoint owns the ``?format=csv|json`` parameter.
    # Disable DRF's renderer override so CSV is not rejected before the view runs.
    "URL_FORMAT_OVERRIDE": None,
}

# One cache for every process (the API's workers and the Temporal worker): throttles and
# the per-minute search limit count across all of them. Without REDIS_URL each process
# keeps its own (fine with one process, locally).
REDIS_URL = config("REDIS_URL", default="")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "leadline",
        }
    }

# Who the visitor is, for throttles (see apps.common.middleware). Behind our own edge
# proxy, the header it sets with the connecting address (Caddy: X-Real-IP); empty uses
# the connection itself. The UI server calls the API for every visitor and vouches
# for their address with UI_PROXY_SECRET (the same value in the UI's environment).
# How long a file transfer ticket (a direct upload or download) stays valid.
TRANSFER_TICKET_SECONDS = config("TRANSFER_TICKET_SECONDS", cast=int, default=600)
# The API's own docs (Swagger at / and /api/docs/). Production turns them off by default.
API_DOCS_ENABLED = config("API_DOCS_ENABLED", cast=bool, default=True)
TRUSTED_CLIENT_IP_HEADER = config("TRUSTED_CLIENT_IP_HEADER", default="")
UI_PROXY_SECRET = config("UI_PROXY_SECRET", default="")

ALLOWED_HOSTS = config("DJANGO_ALLOWED_HOSTS", cast=Csv(), default="localhost,127.0.0.1,testserver")
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", cast=Csv(), default="http://localhost:3000")
CORS_ALLOW_METHODS = ["GET", "PUT", "POST", "DELETE", "PATCH", "OPTIONS"]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", cast=Csv(), default="http://localhost:3000")

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        days=config("JWT_ACCESS_TOKEN_LIFETIME_DAYS", cast=int, default=1)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=config("JWT_REFRESH_TOKEN_LIFETIME_DAYS", cast=int, default=7)
    ),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}
JWT_REFRESH_COOKIE_NAME = config("JWT_REFRESH_COOKIE_NAME", default="leadline_refresh")
JWT_COOKIE_SECURE = config("JWT_COOKIE_SECURE", cast=bool, default=False)
JWT_COOKIE_SAMESITE = config("JWT_COOKIE_SAMESITE", default="Lax")
JWT_COOKIE_DOMAIN = config("JWT_COOKIE_DOMAIN", default="") or None
FRONTEND_URL = config("FRONTEND_URL", default="http://localhost:3000")
ORGANIZATION_INVITATION_EXPIRY_HOURS = config(
    "ORGANIZATION_INVITATION_EXPIRY_HOURS", cast=int, default=168
)
ORGANIZATION_INVITATION_PATH = config("ORGANIZATION_INVITATION_PATH", default="/invitations/accept")
ORGANIZATION_INVITATION_RESEND_COOLDOWN_SECONDS = config(
    "ORGANIZATION_INVITATION_RESEND_COOLDOWN_SECONDS", cast=int, default=60
)
# Per-file limit. A reverse proxy in front of Django must allow bodies at least this large.
ORGANIZATION_DOCUMENT_MAX_UPLOAD_MB = config(
    "ORGANIZATION_DOCUMENT_MAX_UPLOAD_MB", cast=int, default=25
)
EMAIL_BACKEND = config(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = config("EMAIL_HOST", default="localhost")
EMAIL_PORT = config("EMAIL_PORT", cast=int, default=587)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_TIMEOUT = config("EMAIL_TIMEOUT", cast=int, default=10)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", cast=bool, default=True)
EMAIL_USE_SSL = config("EMAIL_USE_SSL", cast=bool, default=False)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="Leadline <noreply@leadline.local>")
PRODUCT_NAME = config("PRODUCT_NAME", default="Leadline")

# Emailed one-time codes (email verification and password reset)
OTP_EXPIRY_MINUTES = config("OTP_EXPIRY_MINUTES", cast=int, default=15)
OTP_MAX_ATTEMPTS = config("OTP_MAX_ATTEMPTS", cast=int, default=5)

REPORTING_MIN_COHORT_SIZE = config("REPORTING_MIN_COHORT_SIZE", cast=int, default=3)

# AI agents. Model ids come only from the environment, one per tier;
# apps/ai/config/models.yaml maps each agent role to a tier. Missing values produce
# system-check warnings rather than errors so the rest of the API still runs.
AI_LLM_PROVIDER = config("AI_LLM_PROVIDER", default="groq")
AI_MODEL_TIERS = {
    "light": config("LIGHT_MODEL", default=""),
    "research": config("RESEARCH_MODEL", default=""),
    "answer": config("ANSWER_MODEL", default=""),
}
GROQ_API_KEY = config("GROQ_API_KEY", default="")
# Backup keys (another account or project): a call the first key can't make, for its
# rate limit or daily cap or because it was rejected, is made with the backup key.
GROQ_API_KEY_2 = config("GROQ_API_KEY_2", default="")
# Fallback provider: calls the primary refuses for its rate limit, or can't answer,
# go to this provider's model for the same tier. Empty AI_FALLBACK_PROVIDER = no
# fallback. Gemini's free tier has its own daily limits, separate from Groq's.
AI_FALLBACK_PROVIDER = config("AI_FALLBACK_PROVIDER", default="")
AI_FALLBACK_TIERS = {
    "light": config("FALLBACK_LIGHT_MODEL", default=""),
    "research": config("FALLBACK_RESEARCH_MODEL", default=""),
    "answer": config("FALLBACK_ANSWER_MODEL", default=""),
}
GEMINI_API_KEY = config("GEMINI_API_KEY", default="")
GEMINI_API_KEY_2 = config("GEMINI_API_KEY_2", default="")
# Each provider's keys, in the order they are tried. A new provider (Claude, OpenAI,
# DeepSeek) adds its key settings here and its models below.
AI_PROVIDER_KEYS = {
    "groq": ["GROQ_API_KEY", "GROQ_API_KEY_2"],
    "gemini": ["GEMINI_API_KEY", "GEMINI_API_KEY_2"],
}
# Each provider's model per tier, as <PROVIDER>_<TIER>_MODEL. Left empty, the default
# provider uses <TIER>_MODEL and the fallback provider FALLBACK_<TIER>_MODEL.
AI_PROVIDER_MODELS = {
    provider: {
        tier: config(f"{provider.upper()}_{tier.upper()}_MODEL", default="")
        for tier in ("light", "research", "answer")
    }
    for provider in AI_PROVIDER_KEYS
}
# Providers that may receive users' private data: company documents and summaries,
# CVs, drafts, decisions. Every role is private unless models.yaml marks it
# `public_inputs` (it reads only public pages and calls). Gemini's free tier is not
# here: Google uses free-tier prompts to improve its products and its terms say not to
# send personal or confidential information. Add a provider once its terms keep the
# data private (Gemini with billing on, Claude, OpenAI).
AI_PRIVATE_DATA_PROVIDERS = config("AI_PRIVATE_DATA_PROVIDERS", cast=Csv(), default="groq")
# Roles try the providers models.yaml lists for them first (then the default order).
# Off: every role uses AI_LLM_PROVIDER, then AI_FALLBACK_PROVIDER.
AI_ROLE_PROVIDERS = config("AI_ROLE_PROVIDERS", cast=bool, default=True)
# A key that reports its rate limit or daily cap is skipped while another option is
# left, for as long as the provider says (at most this long), then tried again.
AI_COOLDOWN_MAX_SECONDS = config("AI_COOLDOWN_MAX_SECONDS", cast=int, default=3600)
# Who hears when the daily model check finds a key rejected or a model gone.
AI_ALERT_EMAILS = config("AI_ALERT_EMAILS", cast=Csv(), default="")

# Agent tools. Tavily is the web search provider; its dev plan allows 100 requests a
# minute, so stay below that (the limit is shared through the cache).
TAVILY_API_KEY = config("TAVILY_API_KEY", default="")
TAVILY_REQUESTS_PER_MINUTE = config("TAVILY_REQUESTS_PER_MINUTE", cast=int, default=60)
# Business directories for the Clients goal (apps/ai/tools/businesses). OpenStreetMap
# needs no key. Google Maps through Apify scrapes Google Maps, which Google's terms do
# not allow: it only runs when APIFY_GOOGLE_MAPS_ENABLED is on, and uses Apify credit.
APIFY_API_TOKEN = config("APIFY_API_TOKEN", default="")
APIFY_GOOGLE_MAPS_ENABLED = config("APIFY_GOOGLE_MAPS_ENABLED", cast=bool, default=False)
# Google PageSpeed Insights (mobile speed of leads' websites). Free key from Google
# Cloud (about 25,000 checks a day); without one the speed check is skipped, because
# Google's shared keyless quota is used up.
PAGESPEED_API_KEY = config("PAGESPEED_API_KEY", default="")
# Rendering services for leads' websites that a plain fetch can't read (sites built
# by JavaScript, or that refuse simple clients). Jina works without a key at a low rate.
JINA_API_KEY = config("JINA_API_KEY", default="")
FIRECRAWL_API_KEY = config("FIRECRAWL_API_KEY", default="")
# Identifies our requests. Add your site once you have a domain, e.g.
# "LeadlineBot/0.1 (+https://yourdomain.com)"; OpenStreetMap refuses placeholder
# domains such as example.com (HTTP 406).
AI_HTTP_USER_AGENT = config("AI_HTTP_USER_AGENT", default="LeadlineBot/0.1")
AI_HTTP_TIMEOUT_SECONDS = config("AI_HTTP_TIMEOUT_SECONDS", cast=float, default=20)
AI_FETCH_MAX_BYTES = config("AI_FETCH_MAX_BYTES", cast=int, default=2_000_000)

# LangSmith tracing of agent runs (off by default; needs LANGSMITH_API_KEY).
LANGSMITH_TRACING = config("LANGSMITH_TRACING", cast=bool, default=False)
LANGSMITH_API_KEY = config("LANGSMITH_API_KEY", default="")
LANGSMITH_PROJECT = config("LANGSMITH_PROJECT", default="leadline")

# Company knowledge: document chunks and their embeddings (pgvector).
# The vector column is sized by KNOWLEDGE_EMBEDDING_DIMENSIONS, so changing the
# embedding model or its dimensions needs a migration plus `manage.py knowledge_reprocess`.
KNOWLEDGE_EMBEDDING_PROVIDER = config("KNOWLEDGE_EMBEDDING_PROVIDER", default="fastembed")
KNOWLEDGE_EMBEDDING_MODEL = config("KNOWLEDGE_EMBEDDING_MODEL", default="BAAI/bge-small-en-v1.5")
KNOWLEDGE_EMBEDDING_DIMENSIONS = config("KNOWLEDGE_EMBEDDING_DIMENSIONS", cast=int, default=384)
# Where fastembed keeps downloaded models; the system temp dir would lose them on reboot.
KNOWLEDGE_EMBEDDING_CACHE_DIR = config(
    "KNOWLEDGE_EMBEDDING_CACHE_DIR", default=str(BASE_DIR / ".cache" / "fastembed")
)
KNOWLEDGE_CHUNK_SIZE = config("KNOWLEDGE_CHUNK_SIZE", cast=int, default=1200)  # characters
KNOWLEDGE_CHUNK_OVERLAP = config("KNOWLEDGE_CHUNK_OVERLAP", cast=int, default=150)
# Text beyond this is ignored, so one huge upload cannot swamp the worker or the index.
KNOWLEDGE_MAX_DOCUMENT_CHARS = config(
    "KNOWLEDGE_MAX_DOCUMENT_CHARS", cast=int, default=400_000
)
# A document stuck in "processing" this long (a worker died) may be reprocessed.
KNOWLEDGE_PROCESSING_TIMEOUT_MINUTES = config(
    "KNOWLEDGE_PROCESSING_TIMEOUT_MINUTES", cast=int, default=30
)
# How much of each document, and of all documents together, the dossier prompt sees.
KNOWLEDGE_DOSSIER_DOCUMENT_CHARS = config(
    "KNOWLEDGE_DOSSIER_DOCUMENT_CHARS", cast=int, default=6_000
)
KNOWLEDGE_DOSSIER_MAX_DOCUMENT_CHARS = config(
    "KNOWLEDGE_DOSSIER_MAX_DOCUMENT_CHARS", cast=int, default=40_000
)
# Edits within this window are folded into one dossier regeneration.
KNOWLEDGE_DOSSIER_DELAY_SECONDS = config(
    "KNOWLEDGE_DOSSIER_DELAY_SECONDS", cast=int, default=30
)

# Discovery runs. DISCOVERY_RUNNER picks what executes a run: "graph" is the real
# agent pipeline (Groq + Tavily + structured sources); "fake" emits realistic
# progress and sample opportunities without calling any model or search API.
DISCOVERY_RUNNER = config("DISCOVERY_RUNNER", default="fake")
DISCOVERY_FAKE_STEP_SECONDS = config("DISCOVERY_FAKE_STEP_SECONDS", cast=float, default=0.8)
# A queued or running run older than this is treated as dead (its worker crashed) and
# no longer blocks new runs. Keep it above the wall-time budget in sources.yaml.
DISCOVERY_STALE_RUN_MINUTES = config("DISCOVERY_STALE_RUN_MINUTES", cast=int, default=20)

# The Proposal Writer. PROPOSAL_RUNNER: "graph" (real agents) or "fake" (sample text,
# no external calls). A job older than PROPOSAL_STALE_MINUTES (its worker died) no
# longer blocks new ones; keep it above the wall-time budget in proposals.yaml.
PROPOSAL_RUNNER = config("PROPOSAL_RUNNER", default="fake")
PROPOSAL_FAKE_STEP_SECONDS = config("PROPOSAL_FAKE_STEP_SECONDS", cast=float, default=0.5)
PROPOSAL_STALE_MINUTES = config("PROPOSAL_STALE_MINUTES", cast=int, default=20)

# Leads and outreach. HUNTER_API_KEY is optional (published business emails by domain).
# Emails go out only when an admin sends them, at most OUTREACH_DAILY_SEND_LIMIT a day
# per organization; the same address is not emailed about the same opportunity again
# within OUTREACH_REPEAT_DAYS unless the admin confirms.
HUNTER_API_KEY = config("HUNTER_API_KEY", default="")
OUTREACH_DAILY_SEND_LIMIT = config("OUTREACH_DAILY_SEND_LIMIT", cast=int, default=20)
OUTREACH_REPEAT_DAYS = config("OUTREACH_REPEAT_DAYS", cast=int, default=7)
OUTREACH_JOB_STALE_MINUTES = config("OUTREACH_JOB_STALE_MINUTES", cast=int, default=10)
# Deadline reminder emails (with a calendar file), this many days before a deadline.
DEADLINE_REMINDER_DAYS = config(
    "DEADLINE_REMINDER_DAYS", cast=Csv(cast=int), default="14,3,1"
)
# An hourly Temporal Schedule starts due daily/weekly saved searches (at most
# DISCOVERY_SCHEDULE_BATCH an hour); turn off to run searches by hand only.
SCHEDULED_RUNS_ENABLED = config("SCHEDULED_RUNS_ENABLED", cast=bool, default=True)
DISCOVERY_SCHEDULE_BATCH = config("DISCOVERY_SCHEDULE_BATCH", cast=int, default=20)
# A run whose hand-over to Temporal failed is retried by the recovery sweep, up to
# this many hand-overs in all, then failed with a clear message.
DISCOVERY_DISPATCH_ATTEMPTS = config("DISCOVERY_DISPATCH_ATTEMPTS", cast=int, default=3)

# Background jobs run on Temporal (`python manage.py temporal_worker`; locally,
# `make temporal-dev` starts a server). JOBS_RUN_INLINE runs them inside the request
# instead, for tests only.
TEMPORAL_ADDRESS = config("TEMPORAL_ADDRESS", default="localhost:7233")
TEMPORAL_NAMESPACE = config("TEMPORAL_NAMESPACE", default="default")
TEMPORAL_TASK_QUEUE = config("TEMPORAL_TASK_QUEUE", default="leadline")
# Jobs at the same time per worker. LLM calls mostly wait on the network, so a few is plenty.
TEMPORAL_MAX_CONCURRENT_ACTIVITIES = config(
    "TEMPORAL_MAX_CONCURRENT_ACTIVITIES", cast=int, default=4
)
JOBS_RUN_INLINE = config("JOBS_RUN_INLINE", cast=bool, default=False)
# How many times Temporal runs a discovery run whose worker died; each retry continues
# within what is left of the run's budget.
DISCOVERY_RUN_ATTEMPTS = config("DISCOVERY_RUN_ATTEMPTS", cast=int, default=2)

# Logging paths (used by development.py and production.py)
LOG_DIR = BASE_DIR / 'logs'
DEBUG_LOG_FILE = 'debug.log'
DEBUG_LOG_PATH = LOG_DIR / DEBUG_LOG_FILE
AUDIT_LOG_FILE = 'audit.log'
AUDIT_LOG_PATH = LOG_DIR / AUDIT_LOG_FILE

if not os.path.exists(LOG_DIR):
    os.mkdir(LOG_DIR)
for path in [DEBUG_LOG_PATH, AUDIT_LOG_PATH]:
    if not os.path.exists(path):
        open(path, 'a').close()