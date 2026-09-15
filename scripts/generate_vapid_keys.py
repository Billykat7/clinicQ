"""Generate a VAPID key pair for web push (Issue 64).

Prints the three settings web push needs, ready to paste into a secret store (a GitHub environment
secret, the server's ``.env``). Nothing is written to disk and nothing is sent anywhere. Generate one
pair per environment and keep it: changing the key invalidates every browser subscription made with the
old one, so every patient would have to allow notifications again.

Usage::

    python -m scripts.generate_vapid_keys --subject mailto:ops@clinicq.example
"""

from __future__ import annotations

import argparse
import base64
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(data: bytes) -> str:
    """base64url without padding, the form browsers and push services use."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate(subject: str) -> dict[str, str]:
    """A new P-256 key pair as the ``WEB_PUSH_VAPID_*`` settings."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return {
        "WEB_PUSH_VAPID_PUBLIC_KEY": _b64url(public),
        "WEB_PUSH_VAPID_PRIVATE_KEY": _b64url(private),
        "WEB_PUSH_VAPID_SUBJECT": subject,
    }


def main(argv: list[str] | None = None) -> int:
    """Print a new key pair; refuse a subject push services would reject."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--subject",
        required=True,
        help="A mailto: address or https: URL push services can contact.",
    )
    args = parser.parse_args(argv)
    if not args.subject.startswith(("mailto:", "https://")):
        parser.error("--subject must start with mailto: or https://")
    for name, value in generate(args.subject).items():
        sys.stdout.write(f"{name}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
