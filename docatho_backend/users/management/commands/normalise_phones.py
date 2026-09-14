"""Rewrite stored phone numbers into E.164 so their owners can sign in.

`User.phone` is a `PhoneNumberField` and keeps whatever it was handed. The apps
look up "+91…"; the admin portal's partner forms saved the bare ten digits, so
a partner onboarded that way was locked out of their own app — "User not found"
about an account the admin had open on screen.

The lookup now tries both spellings, so nobody is locked out either way. This
command is the tidy-up: it makes what is stored match what is sent, so reports,
exports and anything that joins on the number line up.

    uv run python manage.py normalise_phones --dry-run
    uv run python manage.py normalise_phones
"""

from django.core.management.base import BaseCommand

from docatho_backend.users.helper import normalise_phone
from docatho_backend.users.models import User


class Command(BaseCommand):
    help = "Rewrite user phone numbers into E.164 (+91…) form."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        changed = 0
        skipped = 0

        for user in User.objects.exclude(phone="").iterator():
            current = str(user.phone)
            wanted = normalise_phone(current)
            if not wanted or wanted == current:
                continue

            # Another account already holds the normalised form. Rewriting would
            # put two rows on one number, and `phone` is USERNAME_FIELD — that
            # breaks sign-in for both. Report it and leave it for a human.
            clash = User.objects.filter(phone=wanted).exclude(pk=user.pk).first()
            if clash is not None:
                skipped += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  skip #{user.pk} {current} → {wanted} "
                        f"(already held by #{clash.pk})",
                    ),
                )
                continue

            self.stdout.write(f"  #{user.pk} {current} → {wanted}")
            if not dry_run:
                user.phone = wanted
                user.save(update_fields=["phone"])
            changed += 1

        verb = "would update" if dry_run else "updated"
        self.stdout.write(
            self.style.SUCCESS(f"{verb} {changed} number(s), skipped {skipped} clash(es)."),
        )
