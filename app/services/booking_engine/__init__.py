"""AppointHive Booking Engine bridge (Plan 2)."""
from app.services.booking_engine.client import BeClientError, BeNotConnected, BookingEngineClient
from app.services.booking_engine.connect import (
    be_connection_public,
    crm_lead_id_for,
    disconnect_be,
    generate_pairing_code,
    require_be_connected,
    save_manual_connection,
    test_be_connection,
)
from app.services.booking_engine.handoff import (
    apply_be_webhook,
    delivery_dict,
    refresh_delivery,
    send_purchase,
    send_request,
    send_upgrade,
)

__all__ = [
    "BeClientError",
    "BeNotConnected",
    "BookingEngineClient",
    "apply_be_webhook",
    "be_connection_public",
    "crm_lead_id_for",
    "delivery_dict",
    "disconnect_be",
    "generate_pairing_code",
    "refresh_delivery",
    "require_be_connected",
    "save_manual_connection",
    "send_purchase",
    "send_request",
    "send_upgrade",
    "test_be_connection",
]
