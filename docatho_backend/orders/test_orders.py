

def test_serviceable_pincode_accepts_only_raipur():
    """Raipur is the 492xxx block; a "Raipur" elsewhere must not pass.

    The pincode is the authority precisely because the city field is free
    text — Rajasthan and Odisha both have a Raipur, and users type the city
    in every spelling and case.
    """
    from docatho_backend.orders.service_area import is_serviceable_pincode

    assert is_serviceable_pincode("492001")
    assert is_serviceable_pincode("  492099  ")  # trimmed
    assert not is_serviceable_pincode("560038")  # Bengaluru
    assert not is_serviceable_pincode("49200")   # too short
    assert not is_serviceable_pincode("4920011")  # too long
    assert not is_serviceable_pincode("")
    assert not is_serviceable_pincode(None)
