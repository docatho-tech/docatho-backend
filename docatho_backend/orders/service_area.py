"""Where medicine orders can actually be delivered.

Docatho fulfils from Raipur, Chhattisgarh only. The pincode is the authority
rather than the typed city or state: "Raipur" also names places in Rajasthan
and Odisha, and free-text city fields arrive in every spelling and case.

Raipur district is the 492xxx block.

This is the enforcement point. The mobile app checks the same rule while the
customer types so they find out early, but that check is a courtesy — an older
build or a direct API call would skip it.
"""

from __future__ import annotations

import re

SERVICE_AREA_CITY = "Raipur"
SERVICE_AREA_STATE = "Chhattisgarh"
SERVICE_AREA_PINCODE_RE = re.compile(r"^492\d{3}$")

OUT_OF_SERVICE_AREA_DETAIL = (
    f"We only deliver around {SERVICE_AREA_CITY}, {SERVICE_AREA_STATE} at the "
    f"moment. Add an address with a {SERVICE_AREA_CITY} pincode (starting 492) "
    "and we'll get your order moving."
)


def is_serviceable_pincode(postal_code: str | None) -> bool:
    return bool(SERVICE_AREA_PINCODE_RE.match((postal_code or "").strip()))


def is_serviceable_address(address) -> bool:
    """True when `address` is somewhere we can deliver."""
    return is_serviceable_pincode(getattr(address, "postal_code", None))
