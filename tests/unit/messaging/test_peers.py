"""The peer-messaging reachability gate (Issue 1, found while making ``mypy src/`` clean).

``ensure_can_reach`` passed the recipient's id into ``PeerNotReachableError``, whose constructor
takes no arguments on purpose: the refusal must not say anything about the person the caller tried
to reach, because the negative case would otherwise leak the directory. The call raised ``TypeError``
instead, so an unreachable recipient produced a ``500`` rather than the intended ``403``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from src.commons.exceptions import PeerNotReachableError
from src.modules.messaging import peers


def test_unreachable_peer_raises_the_non_disclosing_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(peers, "reachable_peer_ids", lambda db, user_id: set())

    with pytest.raises(PeerNotReachableError) as refused:
        peers.ensure_can_reach(Session(), "sender-id", "target-id")

    # The message names nobody: the caller learns that they may not, never why or about whom.
    assert "target-id" not in str(refused.value)


def test_reachable_peer_passes_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(peers, "reachable_peer_ids", lambda db, user_id: {"target-id"})

    peers.ensure_can_reach(Session(), "sender-id", "target-id")
