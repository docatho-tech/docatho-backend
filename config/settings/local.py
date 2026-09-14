import socket

from .base import *  # noqa: F403

# On by default for local work so the countdown banner and the sweep can be
# seen. Production leaves it at the base default of 0 until the window is a
# decision someone has made — see the note in base.py.
AUTO_ACCEPT_MINUTES = env.int("AUTO_ACCEPT_MINUTES", default=180)  # noqa: F405
from .base import INSTALLED_APPS
from .base import MIDDLEWARE
from .base import env

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = True
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="K2iPjhKVqNHc22qvqqq4dQwa1g9FCxoagxPma3h89LZGjOJ2nf4YxNz1ja33JZ86",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#allowed-hosts
ALLOWED_HOSTS = [
    "localhost",
    "0.0.0.0",
    "127.0.0.1",
    # The Android emulator reaches the host machine at this alias, and a phone
    # on the same wifi reaches it by LAN address. Without them every request
    # from a device answers 400 DisallowedHost, which reads in the app as a
    # bare network failure and sends you looking at the wrong layer.
    "10.0.2.2",
    "65.1.83.112",
    "api.docatho.com",
]

# Plus whatever LAN address this machine currently answers on, so a phone or
# emulator on the same wifi reaches the dev server without this list being
# edited for each new IP. Local settings only — production keeps its fixed list.
ALLOWED_HOSTS += [
    address
    for address in socket.gethostbyname_ex(socket.gethostname())[2]
    if address not in ALLOWED_HOSTS
]

# CACHES
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#caches
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "",
    },
}

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = env(
    "DJANGO_EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

# django-debug-toolbar
# ------------------------------------------------------------------------------
# https://django-debug-toolbar.readthedocs.io/en/latest/installation.html#prerequisites
INSTALLED_APPS += ["debug_toolbar"]
# https://django-debug-toolbar.readthedocs.io/en/latest/installation.html#middleware
MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]
# https://django-debug-toolbar.readthedocs.io/en/latest/configuration.html#debug-toolbar-config
DEBUG_TOOLBAR_CONFIG = {
    "DISABLE_PANELS": [
        "debug_toolbar.panels.redirects.RedirectsPanel",
        # Disable profiling panel due to an issue with Python 3.12+:
        # https://github.com/jazzband/django-debug-toolbar/issues/1875
        "debug_toolbar.panels.profiling.ProfilingPanel",
    ],
    "SHOW_TEMPLATE_CONTEXT": True,
}
# https://django-debug-toolbar.readthedocs.io/en/latest/installation.html#internal-ips
INTERNAL_IPS = ["127.0.0.1", "10.0.2.2"]


# django-extensions
# ------------------------------------------------------------------------------
# https://django-extensions.readthedocs.io/en/latest/installation_instructions.html#configuration
INSTALLED_APPS += ["django_extensions"]

# Your stuff...
# ------------------------------------------------------------------------------
