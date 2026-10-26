"""The pharmacy dashboard's three tabs.

"In Progress" is five statuses, so the app could not ask for it with a single
``?status=``. It used to fetch everything and drop the non-matching rows from
the page it got back, which showed whichever of those ten rows happened to be
in progress and reported the unfiltered total beside the tab.
"""

import pytest

from docatho_backend.orders.models import Order
from docatho_backend.testing.factories import OrderFactory
from docatho_backend.testing.factories import ProviderFactory

pytestmark = pytest.mark.django_db

URL = "/api/providers/chemist-order-list/"


@pytest.fixture
def pharmacy_with_orders():
    provider = ProviderFactory()
    statuses = [
        Order.Status.PLACED,
        Order.Status.APPROVED,
        Order.Status.PACKED,
        Order.Status.OUT_FOR_DELIVERY,
        Order.Status.DELIVERED,
        Order.Status.DELIVERED,
        Order.Status.CANCELLED,
    ]
    for status in statuses:
        OrderFactory(assigned_provider=provider, status=status)
    return provider


def test_bucket_narrows_to_every_status_in_it(auth_client, pharmacy_with_orders):
    client = auth_client(pharmacy_with_orders.user)

    in_progress = client.get(URL, {"bucket": "in_progress"}).data
    assert in_progress["count"] == 3
    assert {row["status"] for row in in_progress["results"]} == {
        "approved",
        "packed",
        "out_for_delivery",
    }

    assert client.get(URL, {"bucket": "new"}).data["count"] == 1
    assert client.get(URL, {"bucket": "completed"}).data["count"] == 2


def test_every_response_counts_all_three_buckets(auth_client, pharmacy_with_orders):
    client = auth_client(pharmacy_with_orders.user)
    # The counts are the tab labels, so they must not move with the tab shown.
    for bucket in ("new", "in_progress", "completed"):
        counts = client.get(URL, {"bucket": bucket}).data["counts"]
        assert counts == {"new": 1, "in_progress": 3, "completed": 2}


def test_unknown_bucket_shows_everything(auth_client, pharmacy_with_orders):
    client = auth_client(pharmacy_with_orders.user)
    assert client.get(URL, {"bucket": "typo"}).data["count"] == 7
