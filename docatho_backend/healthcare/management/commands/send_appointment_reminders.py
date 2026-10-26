from django.core.management.base import BaseCommand

from docatho_backend.healthcare.reminders import REMINDER_MINUTES
from docatho_backend.healthcare.reminders import send_due_reminders


class Command(BaseCommand):
    help = "Notify doctors about appointments starting within the reminder window."

    def add_arguments(self, parser):
        parser.add_argument("--minutes", type=int, default=REMINDER_MINUTES)

    def handle(self, *args, **options):
        sent = send_due_reminders(minutes=options["minutes"])
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} reminder(s)."))
