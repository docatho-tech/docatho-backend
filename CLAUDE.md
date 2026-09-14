# docatho-backend — Django REST API

Python + Django, managed with `uv` (`pyproject.toml`, `uv.lock`). Entry: `manage.py`.
This is the shared backend/API for all three clients (`customer-app`, `provider-app`,
`docatho_dashboard`).

## Figma

No design page maps to the backend directly — it's the API layer. When building
endpoints, the relevant screens are in the client apps' Figma pages:
`customer-app/CLAUDE.md` (User app) and, once indexed, the Provider app / Admin Portal.

Figma file key (shared): `rHKJH65oGtamnJM748MbJo`.

## Things worth knowing before changing a queue

### Status buckets

`docatho_backend/masters/buckets.py` maps each model's statuses into the three groups
the admin queues are worked in, and `apply_bucket` narrows a queryset to one of them
via `?bucket=`. DRF's `filterset_fields` matches a single value, which is why the tab
strips could not be built on it.

**Every status must land in exactly one bucket.** A status in no bucket is a row no
tab can reach — the bug the old dashboard shipped, where three of nine order statuses
were unreachable. `test_admin_portal_surfaces.py::test_every_status_belongs_to_exactly_one_bucket`
checks all four models against their choices, so adding a status to a model without
placing it fails the suite.

### Onboarding vs. the directory

`Provider.onboarding_status` is one field with two audiences. `approved` means the
partner is live and appears in the directory; everything else is the onboarding queue
at `/onboarding`, which asks for `?pipeline=1` (everything not approved). An invited
lab and a live one are the same row at two points in its life, so approving one is a
status change, not a migration.

`AdminProviderCreateSerializer` defaults to **approved**: adding a partner from the
directory means they are live, and the invite wizard sends `invited` explicitly.

### Settlements vs. payouts

`/api/analytics/settlements/` is a live aggregate of what each partner has earned and
is still owed. `orders.Payout` is the ledger of transfers actually initiated, which an
aggregate cannot reconstruct — two payouts of ₹50,000 and one of ₹1,00,000 sum the
same as one of ₹2,00,000. Only **settled** payouts reduce what is owed, and the
serializer keeps `status` and `settled_at` in step so a row can never read "Settled"
without a date.

### Money and comparisons

`RevenueSummaryView` returns `deltas` only when a date range was asked for: "all time"
has no previous period, and growth from zero is a first sale, not "100% up". Both are
`None` rather than a confident number.
