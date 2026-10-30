from app.services.booking_engine.prospect import buyer_fields_from_prospect, first_valid_email


def test_first_valid_email_picks_real_address_from_csv():
    assert first_valid_email("user@domain.com, vick@theguildofmasterbarbers.com") == "vick@theguildofmasterbarbers.com"
    assert first_valid_email("a@b.com, c@d.com") == "a@b.com"
    assert first_valid_email("Name <hello@gentonhair.com.au>") == "hello@gentonhair.com.au"


def test_first_valid_email_skips_placeholders_and_assets():
    assert first_valid_email("n/a") == ""
    assert first_valid_email("user@domain.com") == ""
    assert first_valid_email("whitelogo_315x@2x.png") == ""
    assert first_valid_email("noreply@example.com, real@salon.com.au") == "real@salon.com.au"


def test_buyer_fields_from_prospect_normalizes_multi_email():
    email, name, phone = buyer_fields_from_prospect(
        {
            "business": {"name": "Guild", "email": "user@domain.com, vick@theguildofmasterbarbers.com"},
            "primaryContact": {"name": "Vick", "email": "", "phone": "+61"},
            "contacts": [],
        }
    )
    assert email == "vick@theguildofmasterbarbers.com"
    assert name == "Vick"
    assert phone == "+61"
