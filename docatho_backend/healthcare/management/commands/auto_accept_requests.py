from django.core.management.base import BaseCommand

from docatho_backend.healthcare.auto_accept import auto_accept_overdue


class Command(BaseCommand):
    help = "Accept appointment and diagnostic requests whose answer deadline has passed."

    def handle(self, *args, **options):
        moved = auto_accept_overdue()
        self.stdout.write(
            self.style.SUCCESS(
                f"Auto-accepted {moved['appointments']} appointment(s) "
                f"and {moved['bookings']} booking(s).",
            ),
        )
