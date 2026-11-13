from .base import *  # noqa

DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS += ["debug_toolbar"]
MIDDLEWARE.insert(1, "debug_toolbar.middleware.DebugToolbarMiddleware")

INTERNAL_IPS = config("DJANGO_INTERNAL_IPS", cast=Csv(), default="127.0.0.1")

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'DEBUG',
    },
    # HTTP and model clients log every request and connection at DEBUG; agent runs make
    # hundreds of calls, which buries the app's own logs.
    'loggers': {
        name: {'level': 'WARNING'}
        for name in ('httpx', 'httpcore', 'groq', 'urllib3', 'openai', 'langsmith',
                     'trafilatura', 'htmldate', 'charset_normalizer', 'fastembed')
    },
}