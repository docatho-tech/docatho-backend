from datetime import timedelta

from django.core.management.base import BaseCommand

from docatho_backend.healthcare.video import CALL_WINDOW_AFTER
from docatho_backend.healthcare.video import close_stale_video_consultations


class Command(BaseCommand):
    help = (
        "Complete online consultations still marked in_progress after their "
        "join window closed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=float,
            default=CALL_WINDOW_AFTER / timedelta(hours=1),
            help="How long after the slot a consultation is considered over.",
        )

    def handle(self, *args, **options):
        closed = close_stale_video_consultations(
            grace=timedelta(hours=options["hours"]),
        )
        self.stdout.write(self.style.SUCCESS(f"Closed {closed} consultation(s)."))
