# Copyright The IETF Trust 2024, All Rights Reserved
"""Development-mode Django settings for RPC project"""

from hashlib import sha384

from .base import *

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-gdr8b*13^h9uk#bw$cy#@=-fu_9=&@4^#e&#(b7u3rcbqs_#cl"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []

# Datatracker
DATATRACKER_RPC_API_TOKEN = os.environ["PURPLE_RPC_API_TOKEN"]
DATATRACKER_RPC_API_BASE = "http://host.docker.internal:8000/"
DATATRACKER_API_V1_BASE = "http://host.docker.internal:8000/api/v1"
DATATRACKER_BASE = "http://localhost:8000"


# OIDC configuration (see also base.py)
OIDC_RP_CLIENT_ID = os.environ["PURPLE_OIDC_RP_CLIENT_ID"]
OIDC_RP_CLIENT_SECRET = os.environ["PURPLE_OIDC_RP_CLIENT_SECRET"]
OIDC_OP_ISSUER_ID = "http://localhost:8000/api/openid"
OIDC_OP_JWKS_ENDPOINT = "http://host.docker.internal:8000/api/openid/jwks/"
OIDC_OP_AUTHORIZATION_ENDPOINT = (
    "http://localhost:8000/api/openid/authorize/"  # URL for user agent
)
OIDC_OP_TOKEN_ENDPOINT = "http://host.docker.internal:8000/api/openid/token/"
OIDC_OP_USER_ENDPOINT = "http://host.docker.internal:8000/api/openid/userinfo/"
OIDC_OP_END_SESSION_ENDPOINT = "http://localhost:8000/api/openid/end-session/"

# Misc
SESSION_COOKIE_NAME = (
    "rpcsessionid"  # need to set this if oidc provider is on same domain as client
)


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    "default": {
        # "ENGINE": "django.db.backends.postgresql",
        # "NAME": os.environ.get("POSTGRES_DB"),
        # "USER": os.environ.get("POSTGRES_USER"),
        # "PASSWORD": os.environ.get("POSTGRES_PASSWORD"),
        # "HOST": "db",
        # "PORT": 5432,
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "purple",
        "USER": "postgres",
        "PASSWORD": "postgres",
        "HOST": "host.docker.internal",
        "PORT": 5455,
    }
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {asctime} {message}",
            "style": "{",
        },
        "db": {
            "format": "{asctime} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
        "app_file": {
            "class": "logging.FileHandler",
            "filename": os.path.join("/workspace", "logs/app.log"),
            "formatter": "verbose",
        },
        "db_file": {
            "class": "logging.FileHandler",
            "filename": os.path.join("/workspace", "logs/db.log"),
            "formatter": "db",
        },
    },
    "loggers": {
        "django.db.backends": {
            "level": "DEBUG",
            "handlers": ["db_file"],
            "propagate": False,
        },
        "rpc.blocked_assignments": {
            "level": "INFO",
            "handlers": ["app_file"],
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

# CACHES = {
#     "default": {
#         "BACKEND": "django.core.cache.backends.memcached.PyMemcacheCache",
#         "LOCATION": "memcache:11211",
#         "KEY_PREFIX": "ietf:purple",
#         "KEY_FUNCTION": lambda key, key_prefix, version: (
#             f"{key_prefix}:{version}:{sha384(str(key).encode('utf8')).hexdigest()}"
#         ),
#         "TIMEOUT": 600,  # 10 minute default timeout
#     }
# }

INSTALLED_APPS = INSTALLED_APPS + [
    'debug_toolbar',
    'django_filters',
]

# Add debug toolbar middleware at the beginning
MIDDLEWARE = [
    'debug_toolbar.middleware.DebugToolbarMiddleware',
    'rpc.middleware.APIDebugMiddleware',  # Add this
] + MIDDLEWARE

# Configure for API debugging
DEBUG_TOOLBAR_CONFIG = {
    'SHOW_TOOLBAR_CALLBACK': lambda request: DEBUG,
    'SHOW_COLLAPSED': True,
    'SQL_WARNING_THRESHOLD': 10,  # Warn if more than 10 queries
}

# Allow toolbar to work with API endpoints
INTERNAL_IPS = [
    '127.0.0.1',
    'localhost',
]

# If running in Docker, add this:
import socket
try:
    hostname, _, ips = socket.gethostbyname_ex(socket.gethostname())
    INTERNAL_IPS += ['.'.join(ip.split('.')[:-1] + ['1']) for ip in ips]
except socket.gaierror:
    pass

# Enable SQL panel for API responses
DEBUG_TOOLBAR_PANELS = [
    'debug_toolbar.panels.sql.SQLPanel',
    'debug_toolbar.panels.timer.TimerPanel',
    'debug_toolbar.panels.settings.SettingsPanel',
    'debug_toolbar.panels.headers.HeadersPanel',
    'debug_toolbar.panels.request.RequestPanel',
    'debug_toolbar.panels.staticfiles.StaticFilesPanel',
    'debug_toolbar.panels.templates.TemplatesPanel',
    'debug_toolbar.panels.cache.CachePanel',
    'debug_toolbar.panels.signals.SignalsPanel',
    'debug_toolbar.panels.logging.LoggingPanel',
    'debug_toolbar.panels.redirects.RedirectsPanel',
    'debug_toolbar.panels.profiling.ProfilingPanel',
]
