"""Celery tasks for the healthcare app.

The reminder sweep existed only as a management command, which meant it ran when
somebody remembered to run it — so in practice no reminder ever fired. The
schedule lives in `CELERY_BEAT_SCHEDULE` (config/settings/base.py); this is just
the thin task wrapper beat calls.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def send_appointment_reminders() -> int:
    """Send the 30-minutes-before reminder for every appointment that is due."""
    # Imported inside the task: the module is loaded by autodiscovery at worker
    # start, and `reminders` reaches into `views`.
    from docatho_backend.healthcare.reminders import send_due_reminders

    sent = send_due_reminders()
    logger.info("[reminders] sent=%s", sent)
    return sent
