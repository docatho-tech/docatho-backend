"""Google Places lookups for the delivery-address map picker.

These two endpoints exist only because the Places key cannot live in the app.
A key shipped inside the APK is readable by anyone who unzips it, and Places
web-service calls cannot be locked to an Android package the way the Maps SDK
key is — so the key stays here, IP-restricted to this box, and the app asks us.

That also puts the service area out of the client's reach: the circle below is
the only place results can come from, whatever the app sends.
"""

from __future__ import annotations

import requests
from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

PLACES_BASE = "https://places.googleapis.com/v1"
TIMEOUT_SECONDS = 6

# Raipur, matching `SERVICE_AREA.center` in the app. 25km covers the district
# the pharmacy delivers to and still excludes the Raipurs in Rajasthan and
# Odisha, which is what the plain text search kept returning.
SERVICE_AREA_CENTER = {"latitude": 21.251384, "longitude": 81.629641}
SERVICE_AREA_RADIUS_METRES = 25000.0


class PlacesNotConfiguredError(RuntimeError):
    pass


def _api_key() -> str:
    key = getattr(settings, "GOOGLE_PLACES_API_KEY", "")
    if not key:
        raise PlacesNotConfiguredError("GOOGLE_PLACES_API_KEY is not set")
    return key


class _PlacesView(APIView):
    """Shared plumbing: both views are one upstream call and a reshape."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "places"

    def not_configured(self) -> Response:
        # 503, not 500: the app falls back to the on-device geocoder on this,
        # so a missing key degrades search instead of breaking the screen.
        return Response(
            {"detail": "Place search is unavailable."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    def upstream_failed(self) -> Response:
        return Response(
            {"detail": "Place search is unavailable."},
            status=status.HTTP_502_BAD_GATEWAY,
        )


class PlaceAutocompleteView(_PlacesView):
    """`GET /api/places/autocomplete/?q=` -> predictions inside the service area."""

    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        if not query:
            return Response({"results": []})

        try:
            key = _api_key()
        except PlacesNotConfiguredError:
            return self.not_configured()

        body = {
            "input": query,
            "locationRestriction": {
                "circle": {
                    "center": SERVICE_AREA_CENTER,
                    "radius": SERVICE_AREA_RADIUS_METRES,
                },
            },
            "includedRegionCodes": ["in"],
            "languageCode": "en",
        }
        # The session token bundles a run of keystrokes and the details call
        # that follows into one billed session. The app mints it; we only pass
        # it on, because it has to be the same value across that whole run.
        token = request.query_params.get("session")
        if token:
            body["sessionToken"] = token

        try:
            response = requests.post(
                f"{PLACES_BASE}/places:autocomplete",
                json=body,
                headers={"X-Goog-Api-Key": key},
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            return self.upstream_failed()

        results = []
        for suggestion in payload.get("suggestions", []):
            prediction = suggestion.get("placePrediction")
            if not prediction:
                # Query predictions ("restaurants near me") have no place to
                # drop a pin on, so they are no use to an address form.
                continue
            fmt = prediction.get("structuredFormat", {})
            results.append(
                {
                    "place_id": prediction.get("placeId", ""),
                    "title": fmt.get("mainText", {}).get("text")
                    or prediction.get("text", {}).get("text", ""),
                    "subtitle": fmt.get("secondaryText", {}).get("text", ""),
                },
            )

        return Response({"results": results})


class PlaceDetailsView(_PlacesView):
    """`GET /api/places/details/?place_id=` -> the point to move the pin to."""

    def get(self, request):
        place_id = (request.query_params.get("place_id") or "").strip()
        if not place_id:
            return Response(
                {"detail": "place_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            key = _api_key()
        except PlacesNotConfiguredError:
            return self.not_configured()

        params = {}
        token = request.query_params.get("session")
        if token:
            params["sessionToken"] = token

        try:
            response = requests.get(
                f"{PLACES_BASE}/places/{place_id}",
                params=params,
                headers={
                    "X-Goog-Api-Key": key,
                    # Billing is per requested field group, so ask for the two
                    # the picker uses and nothing else.
                    "X-Goog-FieldMask": "location,formattedAddress",
                },
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            return self.upstream_failed()

        location = payload.get("location") or {}
        if "latitude" not in location or "longitude" not in location:
            return self.upstream_failed()

        return Response(
            {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "formatted_address": payload.get("formattedAddress", ""),
            },
        )
