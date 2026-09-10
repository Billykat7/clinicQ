"""Peer-to-peer messaging: who may open a direct thread with whom.

A record-anchored thread derives its participants from the record it is about. A **peer** thread
has no such record — it is two people talking directly — so something else has to decide whether
one may reach the other, or the compose screen becomes a directory of every user in the system.

That relationship is a domain fact, and this module is the single place it is resolved. The kernel
ships the seam and the safe default: **nobody is reachable**. Fill in
:func:`list_reachable_peers` with whatever "these two have a reason to talk" means in your domain —
they belong to the same organisation, work the same case, occupy the same building — and the rest
of messaging follows without further edits.

Two clinicq the implementation must keep, both of which the empty default trivially satisfies
and a careless one loses:

* **The list and the gate agree.** :func:`can_reach` is derived from
  :func:`list_reachable_peers`, so a person the compose screen offers is always a person the send
  will accept, and the reverse. Do not write the membership test twice.
* **A refusal reveals nothing.** :func:`ensure_can_reach` raises
  :class:`~src.commons.exceptions.PeerNotReachableError` (403) identically for "no such user" and
  "not reachable", so the error cannot be used to probe who exists or where they are.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.commons.exceptions import PeerNotReachableError


@dataclass(frozen=True)
class Peer:
    """A user the caller may message: their user id and a display name, never their contact."""

    user_id: str
    name: str | None


def list_reachable_peers(db: Session, user_id: str) -> list[Peer]:
    """Return the users ``user_id`` may open a direct thread with, excluding themselves.

    The one query the compose entry point renders from: it surfaces **only** reachable people, so
    a caller is never shown — and can never enumerate — anyone they have no relationship with.
    De-duplicate by user id if your rule can match the same person twice.

    Empty in the kernel: with no domain, no two users have a reason to talk yet. A peer thread
    cannot be opened until this returns someone, which is the correct closed default.
    """
    return []


def reachable_peer_ids(db: Session, user_id: str) -> set[str]:
    """Return just the user ids of ``user_id``'s reachable peers (the membership set)."""
    return {peer.user_id for peer in list_reachable_peers(db, user_id)}


def can_reach(db: Session, user_id: str, other_user_id: str) -> bool:
    """Whether ``user_id`` may open a direct thread with ``other_user_id``."""
    return other_user_id in reachable_peer_ids(db, user_id)


def ensure_can_reach(db: Session, user_id: str, other_user_id: str) -> None:
    """Raise :class:`PeerNotReachableError` (403) unless the two may message directly.

    The server-side gate a resolved recipient is validated against before a peer thread is opened.
    """
    if not can_reach(db, user_id, other_user_id):
        # No argument on purpose: the refusal must say nothing about the recipient, or the negative
        # case would leak who exists and where they live.
        raise PeerNotReachableError()
