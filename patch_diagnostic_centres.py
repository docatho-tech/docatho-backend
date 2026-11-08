"""Fill in the Raipur diagnostic centres' location fields on a remote backend.

The three centres whose rate cards were imported on 2026-11-08 have no address,
locality or coordinates, so the patient app cannot render "Mowa · 2.3km away" on
any of them. The values below were read off each centre's Google Maps listing on
2026-11-08 (one unambiguous match each, searched as "<name> Raipur Chhattisgarh").

Runs from a developer machine against the admin CRUD API, same as
``seed_prod_via_api.py`` — there is no way to type coordinates into the admin
portal, because its provider form has no inputs for the new fields yet.

    uv run python patch_diagnostic_centres.py --host https://api.docatho.com \
        --phone +919999999999 --password 'secret'            # dry run
    uv run python patch_diagnostic_centres.py --host ... --apply

Dry run by default: it prints the PATCH it would send, field by field, and
changes nothing.

Deliberately NOT imported from Maps:

* **Google's rating and review count.** ``rating_avg``/``review_count`` are
  Docatho's own review aggregate; Google's 4.5 (353) in those fields would make
  the card claim 353 Docatho reviews over an empty review list, and the first
  real review would average into a number Docatho never earned. The cards hide
  the badge while the count is 0 instead.
* **Photos.** Maps photos belong to Google or to whoever uploaded them, and the
  media bucket is public. The centres have to supply their own.
* **The Maps display names.** All three are SEO-stuffed ("True Diagnostics -
  Diagnostic Center| Pathology Lab| …"). ``name`` keeps the clean version.
"""

import argparse
import sys

import requests

TIMEOUT = 30

# Keyed by the centre's name in the database, which is matched case-insensitively
# and must be unique on the host — this script will not guess between two rows.
CENTRES = {
    "True Diagnostics": {
        "city": "Raipur",
        "location": "Mowa",
        "address_line1": "#15, 1st Floor, Usha Pride, Mowa",
        "pincode": "492014",
        "latitude": "21.266996",
        "longitude": "81.664082",
        "map_url": "https://www.google.com/maps/place/?q=place_id:ChIJb2MsYfrdKDoRcyW7_SvWLNY",
    },
    "HealthMark": {
        "city": "Raipur",
        "location": "Shankar Nagar",
        "address_line1": (
            "Shop No. 2, VIP Club Road, opp. Ashoka Ratna, Shankar Nagar"
        ),
        "pincode": "492014",
        "latitude": "21.262035",
        "longitude": "81.675567",
        "map_url": "https://www.google.com/maps/place/?q=place_id:ChIJtWRqDADdKDoRqS6W5qz9M7E",
        # Created by the catalogue import with +910000000001, which is
        # unallocatable in India (mobiles start 6-9), so no handset can ever
        # receive its login OTP. Forced because the field is not empty.
        "phone": "+919853855980",
    },
    "Triveni Diagnostics": {
        "city": "Raipur",
        "location": "Telibandha",
        "address_line1": "Atal Path Expressway, opposite Flower Story, Telibandha",
        "pincode": "492001",
        "latitude": "21.239830",
        "longitude": "81.667994",
        "map_url": "https://www.google.com/maps/place/?q=place_id:ChIJHRKvcRzdKDoRHD-h-Luffcg",
    },
}

# Fields written even when the row already holds something else. Everything else
# is only filled in when it is empty, so a value an operator typed by hand is
# never silently replaced by a scraped one.
FORCE = {("HealthMark", "phone")}

EMPTY = ("", None, "0", "0.00", 0)


def login(host: str, phone: str, password: str) -> str:
    response = requests.post(
        f"{host}/api/admin-login/",
        json={"phone": phone, "password": password},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        sys.exit(f"admin-login failed [{response.status_code}]: {response.text[:300]}")
    return response.json()["token"]


def fetch_centres(host: str, headers: dict) -> list[dict]:
    """Every diagnostic partner on the host, across onboarding statuses."""
    rows: list[dict] = []
    for provider_type in ("Diagnostic Center", "Lab"):
        url = f"{host}/api/providers/admin/list/"
        params = {"provider_type": provider_type, "page_size": 100}
        while url:
            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=TIMEOUT,
            )
            if response.status_code != 200:
                sys.exit(f"list failed [{response.status_code}]: {response.text[:300]}")
            payload = response.json()
            rows.extend(payload.get("results", payload))
            url = payload.get("next") if isinstance(payload, dict) else None
            params = None
    return rows


def plan_for(row: dict, wanted: dict) -> tuple[dict, list[str]]:
    """Split the wanted values into what to send and what to leave alone."""
    patch, kept = {}, []
    for field, value in wanted.items():
        current = row.get(field)
        if (row["name"], field) in FORCE or current in EMPTY:
            patch[field] = value
        elif str(current) != str(value):
            kept.append(f"{field}: keeping {current!r}, Maps says {value!r}")
    return patch, kept


def self_check() -> None:
    """`--self-check`: the fill-empty-but-never-overwrite rule, asserted."""
    row = {
        "name": "True Diagnostics",
        "location": "",
        "city": "Raipur",
        "pincode": "492099",
        "latitude": None,
        "rating_avg": "0.00",
    }
    patch, kept = plan_for(
        row,
        {
            "location": "Mowa",
            "city": "Raipur",
            "pincode": "492014",
            "latitude": "21.266996",
            "rating_avg": "4.0",
        },
    )
    assert patch["location"] == "Mowa", patch  # empty string gets filled
    assert patch["latitude"] == "21.266996", patch  # None gets filled
    assert patch["rating_avg"] == "4.0", patch  # "0.00" counts as empty
    assert "city" not in patch, patch  # already correct, left alone
    assert "pincode" not in patch, patch  # disagrees, so the operator's wins
    assert kept == ["pincode: keeping '492099', Maps says '492014'"], kept

    forced, _ = plan_for(
        {"name": "HealthMark", "phone": "+910000000001"},
        {"phone": "+919853855980"},
    )
    assert forced == {"phone": "+919853855980"}, forced  # FORCE beats non-empty
    print("self-check ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    if "--self-check" in sys.argv:
        self_check()
        return
    parser.add_argument("--host", required=True)
    parser.add_argument("--phone", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually PATCH; without it the script only prints what it would do",
    )
    args = parser.parse_args()
    host = args.host.rstrip("/")

    headers = {"Authorization": f"Token {login(host, args.phone, args.password)}"}
    rows = fetch_centres(host, headers)
    by_name: dict[str, list[dict]] = {}
    for row in rows:
        by_name.setdefault(row["name"].strip().lower(), []).append(row)

    print(f"{len(rows)} diagnostic partners on {host}\n")
    for name, wanted in CENTRES.items():
        matches = by_name.get(name.lower(), [])
        if len(matches) != 1:
            print(f"SKIP {name}: {len(matches)} rows match that name — fix by hand\n")
            continue
        row = matches[0]
        patch, kept = plan_for(row, wanted)
        print(f"{name} (id {row['id']}, {row['onboarding_status']})")
        for line in kept:
            print(f"  kept  {line}")
        if not patch:
            print("  nothing to write\n")
            continue
        for field, value in patch.items():
            print(f"  write {field} = {value!r}")
        if not args.apply:
            print("  (dry run)\n")
            continue
        response = requests.patch(
            f"{host}/api/providers/admin/{row['id']}/",
            headers=headers,
            json=patch,
            timeout=TIMEOUT,
        )
        if response.status_code != 200:
            print(f"  FAILED [{response.status_code}] {response.text[:300]}\n")
            continue
        print("  ok\n")

    untouched = [
        row["name"]
        for row in rows
        if row["name"].strip().lower() not in {n.lower() for n in CENTRES}
        and row.get("onboarding_status") == "approved"
    ]
    if untouched:
        print("Approved centres this script has no Maps data for:")
        for name in untouched:
            print(f"  {name}")


if __name__ == "__main__":
    main()
