"""The Places proxy behind the delivery-address search box."""

from unittest import mock

import pytest
import requests

from docatho_backend.testing.factories import UserFactory

pytestmark = pytest.mark.django_db

AUTOCOMPLETE_RESPONSE = {
    "suggestions": [
        {
            "placePrediction": {
                "placeId": "ChIJ_telibandha",
                "text": {"text": "Telibandha, Raipur, Chhattisgarh"},
                "structuredFormat": {
                    "mainText": {"text": "Telibandha"},
                    "secondaryText": {"text": "Raipur, Chhattisgarh, India"},
                },
            },
        },
        # Google also returns searches with no place attached; an address form
        # has nowhere to put those.
        {"queryPrediction": {"text": {"text": "chemists near telibandha"}}},
    ],
}


def _ok(payload):
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_autocomplete_returns_predictions_restricted_to_the_service_area(
    auth_client,
    settings,
):
    settings.GOOGLE_PLACES_API_KEY = "test-key"
    client = auth_client(UserFactory())

    with mock.patch("requests.post", return_value=_ok(AUTOCOMPLETE_RESPONSE)) as post:
        resp = client.get("/api/places/autocomplete/", {"q": "teli", "session": "s-1"})

    assert resp.status_code == 200
    assert resp.data["results"] == [
        {
            "place_id": "ChIJ_telibandha",
            "title": "Telibandha",
            "subtitle": "Raipur, Chhattisgarh, India",
        },
    ]

    body = post.call_args.kwargs["json"]
    assert body["input"] == "teli"
    assert body["sessionToken"] == "s-1"
    # The restriction is ours, not the app's: Raipur also exists in Rajasthan.
    circle = body["locationRestriction"]["circle"]
    assert circle["center"] == {"latitude": 21.251384, "longitude": 81.629641}
    assert circle["radius"] == 25000.0
    assert post.call_args.kwargs["headers"]["X-Goog-Api-Key"] == "test-key"


def test_details_returns_the_point_to_move_the_pin_to(auth_client, settings):
    settings.GOOGLE_PLACES_API_KEY = "test-key"
    client = auth_client(UserFactory())
    payload = {
        "location": {"latitude": 21.23983, "longitude": 81.667994},
        "formattedAddress": "Telibandha, Raipur, Chhattisgarh 492001, India",
    }

    with mock.patch("requests.get", return_value=_ok(payload)):
        resp = client.get("/api/places/details/", {"place_id": "ChIJ_telibandha"})

    assert resp.status_code == 200
    assert resp.data["latitude"] == pytest.approx(21.23983)
    assert resp.data["longitude"] == pytest.approx(81.667994)


def test_without_a_key_search_is_unavailable_rather_than_broken(auth_client, settings):
    """503 is what tells the app to fall back to the phone's own geocoder."""
    settings.GOOGLE_PLACES_API_KEY = ""
    client = auth_client(UserFactory())

    resp = client.get("/api/places/autocomplete/", {"q": "teli"})

    assert resp.status_code == 503


def test_upstream_failure_is_a_502_not_a_500(auth_client, settings):
    settings.GOOGLE_PLACES_API_KEY = "test-key"
    client = auth_client(UserFactory())

    with mock.patch("requests.post", side_effect=requests.Timeout):
        resp = client.get("/api/places/autocomplete/", {"q": "teli"})

    assert resp.status_code == 502


def test_search_needs_a_login(client):
    assert client.get("/api/places/autocomplete/", {"q": "teli"}).status_code == 401
