from app.services.booking_engine.client import BeClientError
from app.services.booking_engine.errors import be_http_detail, friendly_be_message


def test_validation_buyer_email_is_actionable():
    msg = friendly_be_message(
        raw="Validation failed",
        status=400,
        body={"error": "Validation failed", "details": {"buyerEmail": ["Invalid input"]}},
    )
    assert "valid buyer email" in msg.lower()
    assert "contact tab" in msg.lower()


def test_unauthorized_asks_to_reconnect():
    msg = friendly_be_message(raw="Unauthorized", status=401, body={"error": "Unauthorized"})
    assert "connect" in msg.lower()
    assert "pair" in msg.lower()


def test_no_payment_provider():
    msg = friendly_be_message(
        raw="No payment provider configured",
        status=400,
        body={"error": "No payment provider configured"},
    )
    assert "stripe" in msg.lower() or "paypal" in msg.lower()


def test_be_http_detail_uses_friendly_error():
    exc = BeClientError(
        "Validation failed",
        status=400,
        body={"error": "Validation failed", "details": {"buyerEmail": ["Invalid input"]}},
    )
    detail = be_http_detail(exc)
    assert detail["code"] == "APPOINTHIVE_BRIDGE"
    assert "valid buyer email" in detail["error"].lower()
    assert "Validation failed" not in detail["error"] or "buyer email" in detail["error"].lower()


def test_readable_be_message_preserved():
    msg = friendly_be_message(
        raw="Revision is not FINALIZED",
        status=400,
        body={"error": "Revision is not FINALIZED"},
    )
    assert msg == "Revision is not FINALIZED"
