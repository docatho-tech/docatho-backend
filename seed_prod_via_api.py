"""Push the local medicine catalogue to a remote docatho backend over its REST API.

Runs on a developer machine: reads Category/Medicine straight out of the local
database and re-creates them on the target host through the admin CRUD
endpoints. Exists because the source CSVs (235 MB) cannot be copied to the
production box, so the ~19k rows have to travel as HTTP requests instead.

    uv run python seed_prod_via_api.py --host https://api.docatho.com \
        --phone +919999999999 --password 'secret'

Resumable: every medicine that lands is appended to a progress file keyed by
(name, manufacturer), so an interrupted run picks up where it stopped instead of
duplicating rows. Delete the progress file to force a full re-push.
"""

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import django
import requests

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
sys.path.append(str(Path(__file__).resolve().parent))
django.setup()

from docatho_backend.medicines.models import Category  # noqa: E402
from docatho_backend.medicines.models import Medicine  # noqa: E402

PROGRESS_FILE = Path(__file__).with_name(".seed_prod_progress.jsonl")


def login(host: str, phone: str, password: str) -> str:
    response = requests.post(
        f"{host}/api/admin-login/",
        json={"phone": phone, "password": password},
        timeout=30,
    )
    if response.status_code != 200:
        sys.exit(f"admin-login failed [{response.status_code}]: {response.text[:300]}")
    return response.json()["token"]


def session_for(token: str) -> requests.Session:
    session = requests.Session()
    session.headers["Authorization"] = f"Token {token}"
    # 19k requests over one host: reuse connections rather than opening a socket
    # per medicine, which is what makes the difference between minutes and hours.
    adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def push_categories(session: requests.Session, host: str) -> dict[int, int]:
    """Create every local category remotely; return local id -> remote id."""
    existing = {}
    url = f"{host}/api/medicines/categories/?page_size=200"
    while url:
        payload = session.get(url, timeout=60).json()
        for row in payload["results"]:
            existing[row["name"]] = row["id"]
        url = payload["next"]

    mapping = {}
    for category in Category.objects.all():
        if category.name in existing:
            mapping[category.id] = existing[category.name]
            continue
        response = session.post(
            f"{host}/api/medicines/categories/",
            json={
                "name": category.name,
                "description": category.description,
                "image_url": category.image_url or "",
                "is_active": category.is_active,
            },
            timeout=60,
        )
        if response.status_code not in (200, 201):
            sys.exit(f"category {category.name!r} failed: {response.text[:300]}")
        mapping[category.id] = response.json()["id"]
    return mapping


def load_done() -> set[str]:
    if not PROGRESS_FILE.exists():
        return set()
    with PROGRESS_FILE.open(encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="e.g. https://api.docatho.com")
    parser.add_argument("--phone", required=True, help="admin phone (USERNAME_FIELD)")
    parser.add_argument("--password", required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, help="push at most N medicines (smoke test)")
    args = parser.parse_args()

    host = args.host.rstrip("/")
    session = session_for(login(host, args.phone, args.password))

    category_map = push_categories(session, host)
    print(f"categories: {len(category_map)} mapped")

    done = load_done()
    queryset = Medicine.objects.prefetch_related("category").order_by("id")
    medicines = [m for m in queryset if f"{m.name}|{m.manufacturer}" not in done]
    if args.limit:
        medicines = medicines[: args.limit]
    print(f"medicines: {len(medicines)} to push, {len(done)} already done")

    progress = PROGRESS_FILE.open("a", encoding="utf-8")
    lock = threading.Lock()
    counters = {"ok": 0, "fail": 0}

    def push(medicine: Medicine) -> None:
        body = {
            "name": medicine.name,
            "brand": medicine.brand or "",
            "manufacturer": medicine.manufacturer or "",
            "content": medicine.content or "",
            "description": medicine.description or "",
            "image_url": medicine.image_url or "",
            "price": str(medicine.price),
            "mrp": str(medicine.mrp),
            "stock": medicine.stock,
            "schedule": medicine.schedule,
            "is_active": medicine.is_active,
            "category_ids": [category_map[c.id] for c in medicine.category.all()],
        }
        try:
            response = session.post(f"{host}/api/medicines/", json=body, timeout=60)
        except requests.RequestException as exc:
            with lock:
                counters["fail"] += 1
                print(f"  ! {medicine.name}: {exc}")
            return

        with lock:
            if response.status_code in (200, 201):
                counters["ok"] += 1
                progress.write(f"{medicine.name}|{medicine.manufacturer}\n")
                # Flush per row: a kill -9 mid-run must not replay work that the
                # server already committed, or the re-run creates duplicates.
                progress.flush()
            else:
                counters["fail"] += 1
                print(f"  ! {medicine.name} [{response.status_code}] {response.text[:200]}")
            total = counters["ok"] + counters["fail"]
            if total % 500 == 0:
                print(f"  ...{total}/{len(medicines)} (ok {counters['ok']})")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(push, medicines))
    progress.close()

    print(json.dumps(counters))
    remote_total = session.get(f"{host}/api/medicines/?page_size=1", timeout=60).json()
    print(f"remote medicine count: {remote_total['count']}")


if __name__ == "__main__":
    main()
