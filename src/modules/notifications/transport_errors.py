"""The two ways a transport can fail (Issue 63), in a module of their own.

Kept out of the ``transports`` package so the modules a transport is built on (the web push sender,
Issue 64) can raise them without importing the package that imports them back.

* :class:`TransportError`: the provider could not take the message *this time* (a timeout, a 5xx).
* :class:`PermanentTransportError`: the address will never work (a revoked push subscription, a
  malformed number), so the row ends at once and the next transport in the chain is tried.
"""


class TransportError(Exception):
    """The provider did not accept the message this time; worth retrying."""


class PermanentTransportError(TransportError):
    """The address can never receive this message; retrying would only repeat the failure."""
