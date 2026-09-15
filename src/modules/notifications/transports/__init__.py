"""Transport adapters for patient notifications: web push, WhatsApp, SMS and a no-op (Issue 63).

The notification service picks one per message, free transports first (:data:`~src.commons.enums.PATIENT_TRANSPORT_CHAIN`);
nothing outside :mod:`src.modules.notifications` calls a transport.
"""

from src.modules.notifications.transports.base import (
    PatientAddresses,
    PermanentTransportError,
    Transport,
    TransportError,
    TransportReceipt,
)
from src.modules.notifications.transports.noop import NoopTransport
from src.modules.notifications.transports.registry import (
    TransportSet,
    active_transports,
    build_transports,
    use_transports,
)

__all__ = [
    "NoopTransport",
    "PatientAddresses",
    "PermanentTransportError",
    "Transport",
    "TransportError",
    "TransportReceipt",
    "TransportSet",
    "active_transports",
    "build_transports",
    "use_transports",
]
