from django.core.management.base import BaseCommand

from docatho_backend.medicines.models import Category


class Command(BaseCommand):
    help = "Create categories. Provide names comma-separated with --names or a file path with --file"

    def add_arguments(self, parser):
        parser.add_argument("--names", type=str, help="Comma separated category names")
        parser.add_argument(
            "--file", type=str, help="Path to newline-separated names file"
        )

    def handle(self, *args, **options):
        names = []
        if options.get("names"):
            names.extend([n.strip() for n in options["names"].split(",") if n.strip()])
        if options.get("file"):
            try:
                with open(options["file"], "r", encoding="utf-8") as fh:
                    for line in fh:
                        n = line.strip()
                        if n:
                            names.append(n)
            except Exception as exc:
                self.stderr.write(f"Failed to read file: {exc}")
                return

        if not names:
            self.stderr.write("No category names provided.")
            return

        created = 0
        existed = 0
        for name in names:
            obj, created_flag = Category.objects.get_or_create(name=name)
            if created_flag:
                created += 1
            else:
                existed += 1

        self.stdout.write(
            f"Categories processed. created={created} already_existed={existed}"
        )
