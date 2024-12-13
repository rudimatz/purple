# Copyright The IETF Trust 2024, All Rights Reserved
"""RFC Editor db transfer-mode Django settings for RPC project

Development mode with some additional tweaks.
"""

from .development import *

# Add the rfced app
INSTALLED_APPS.append("rfced.apps.RfcedConfig")

# Add the rfced database
DATABASES |= {
    "rfced": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get("MARIADB_DATABASE"),
        "USER": os.environ.get("MARIADB_USER"),
        "PASSWORD": os.environ.get("MARIADB_PASSWORD"),
        "HOST": "rfced",
    },
}

DATABASE_ROUTERS = ["rfced.routers.RfcedRouter"]

