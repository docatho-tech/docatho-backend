"""
With these settings, tests run faster.
"""

from .base import *  # noqa: F403
from .base import TEMPLATES
from .base import env

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="MdOUf9aDzrE2aRhWLgyqwSUcBIYQ7gyqqrVQVx7uadXFKXpWdZ0cB4MYQu2FTkGI",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#test-runner
TEST_RUNNER = "django.test.runner.DiscoverRunner"

# PASSWORDS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# DEBUGGING FOR TEMPLATES
# ------------------------------------------------------------------------------
TEMPLATES[0]["OPTIONS"]["debug"] = True  # type: ignore[index]

# MEDIA
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = "http://media.testserver/"

# base.py switches media to S3 when AWS credentials are present, and the test
# settings inherit the developer's `.env`. Without this the suite would write
# every uploaded fixture into the live bucket — the same trap the HMS keys
# below were blanked for.
AWS_ACCESS_KEY_ID = ""
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
# 100ms
# ------------------------------------------------------------------------------
# Forced off. `base.py` reads real credentials from `.env`, and with them set
# `ensure_video_room` calls the live 100ms API during unit tests — that made the
# suite network-dependent and littered the account with `docatho-appt-*` rooms.
# Blanking the keys puts the video tests back on `dev_mock_token`, which is what
# they assert against. Live coverage lives elsewhere: `manage.py check_hms` and
# the Playwright suite (`docatho_dashboard/e2e/video-call.spec.ts`).
HMS_APP_ACCESS_KEY = ""
HMS_APP_SECRET = ""
HMS_TEMPLATE_ID = ""

# Your stuff...
# ------------------------------------------------------------------------------
