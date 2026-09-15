"""The post-visit feedback answer page, ``/f/{token}`` (Issue 87).

Opened from the feedback message (a web push opens it directly; an SMS says to reply with a digit instead).
The page is the question and five large buttons: **one tap on a score answers**, with whatever comment was
typed above it. No sign-in: the link is the secret. A link that finds nothing, or a request that was never
sent, gets the same "not found" page, so a link cannot be probed for.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, Request, status
from fastapi.responses import Response

from src.commons.exceptions import NotFoundError
from src.core.config import get_settings
from src.modules.appointments import feedback
from src.web.context import short_session
from src.web.routes import templates

router = APIRouter(prefix="/f", include_in_schema=False)

NO_STORE: Final = {"Cache-Control": "no-store"}


@router.get("/{token}", name="feedback_page")
def feedback_page(token: str, request: Request) -> Response:
    """The question for one visit, or a page saying the link opens nothing (404)."""
    settings = get_settings()
    with short_session(request) as db:
        try:
            state = feedback.request_view(db, feedback.answerable(db, token))
        except NotFoundError:
            state = None
    response = templates.TemplateResponse(
        request,
        "feedback/answer.html" if state else "feedback/missing.html",
        {
            "settings": settings,
            "app_name": settings.app_name,
            "page_title": "How was your visit?",
            "feedback": state,
            "state_json": state.model_dump(mode="json") if state else None,
        },
        status_code=status.HTTP_200_OK if state else status.HTTP_404_NOT_FOUND,
    )
    response.headers.update(NO_STORE)
    return response
