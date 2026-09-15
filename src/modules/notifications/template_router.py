"""The notification template editor's API (Issue 66): what each patient message says, and changing it safely.

For operators (the ``logs`` grant, like the delivery viewer and the SMS kill switch), because a template is
platform-wide: every clinic's patients receive the same words.

* ``GET /notifications/templates``: every patient message template, per channel and language, as sent now.
* ``GET /notifications/templates/{template}/{channel}/{language}``: one, with the blanks it may use and every
  stored version.
* ``POST /notifications/templates/preview``: exactly what a patient would receive for some words, with the SMS
  character and segment count, and whether the words may be published. Nothing is stored.
* ``POST /notifications/templates/{template}/{channel}/{language}/versions``: publish words as a new version,
  sent from the next message on; ``422`` with the reason when they break the variable contract.
* ``GET /notifications/templates/versions/{version_id}``: one stored version, to reproduce a message a ledger
  row names.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.rbac_deps import require
from src.commons.enums import (
    NOTIFICATION_LANGUAGES,
    BoardLanguage,
    GrantScope,
    NotificationChannel,
    NotificationTemplate,
    TemplateSource,
)
from src.core.security import get_current_user, resolve_active_user
from src.database.models.notification_template import NotificationTemplateVersion
from src.database.session import get_db
from src.modules.notifications import template_registry as registry
from src.modules.notifications.schemas import (
    TemplateDraftIn,
    TemplateKeyOut,
    TemplateListOut,
    TemplatePreviewIn,
    TemplatePreviewOut,
    TemplateVersionOut,
)
from src.modules.notifications.sms_segments import count_segments, to_gsm7

router = APIRouter(prefix="/notifications/templates", tags=["notifications"])

DbSession = Annotated[Session, Depends(get_db)]
TemplatesRead = Annotated[
    None, Depends(require("logs", "read", scope=GrantScope.BUSINESS))
]
TemplatesWrite = Annotated[
    None, Depends(require("logs", "update", scope=GrantScope.BUSINESS))
]
CurrentUser = Annotated[dict, Depends(get_current_user)]


def _language(code: str) -> BoardLanguage:
    """A notification language by its code, or 404."""
    for language in NOTIFICATION_LANGUAGES:
        if language.value == code:
            return language
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such language.")


def _patient_template(
    template: NotificationTemplate, channel: NotificationChannel
) -> None:
    """404 unless the pair is a patient message the registry holds."""
    if (
        template not in registry.PATIENT_TEMPLATES
        or channel not in registry.PATIENT_CHANNELS
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such template.")


def _stored_out(row: NotificationTemplateVersion) -> TemplateVersionOut:
    return TemplateVersionOut(
        id=row.id,
        template=NotificationTemplate(row.template_key),
        channel=NotificationChannel(row.channel),
        language=row.language,
        version=row.version,
        subject=row.subject,
        body=row.body,
        source=TemplateSource(row.source),
        created_by=row.created_by,
        reviewed_by=row.reviewed_by,
        created_at=row.created_at,
    )


def _stored(
    db: Session,
    template: NotificationTemplate,
    channel: NotificationChannel,
    language: BoardLanguage,
) -> list[NotificationTemplateVersion]:
    """Every stored version for a key, newest first."""
    return list(
        db.execute(
            select(NotificationTemplateVersion)
            .where(
                NotificationTemplateVersion.template_key == template.value,
                NotificationTemplateVersion.channel == channel.value,
                NotificationTemplateVersion.language == language.value,
            )
            .order_by(NotificationTemplateVersion.version.desc())
        ).scalars()
    )


def _current_out(
    db: Session,
    template: NotificationTemplate,
    channel: NotificationChannel,
    language: BoardLanguage,
) -> TemplateVersionOut:
    """What is sent now, without storing anything: the newest stored version, or the locale file's."""
    text = registry.builtin_text(template, channel, language)
    stored = _stored(db, template, channel, BoardLanguage(text.language))
    if stored and stored[0].version >= text.version:
        return _stored_out(stored[0])
    return TemplateVersionOut(
        template=template,
        channel=channel,
        language=text.language.value,
        version=text.version,
        subject=text.subject,
        body=text.body,
        source=TemplateSource.BUILTIN,
        reviewed_by=text.reviewed_by,
    )


@router.get("", response_model=TemplateListOut)
def list_templates(_: TemplatesRead, db: DbSession) -> TemplateListOut:
    """Every patient message template in every channel and written language, as it is sent now."""
    written = registry.languages()
    items = [
        _current_out(db, template, channel, language)
        for template in sorted(registry.PATIENT_TEMPLATES)
        for channel in registry.PATIENT_CHANNELS
        for language in written
    ]
    return TemplateListOut(
        languages=[language.value for language in written], items=items
    )


@router.post("/preview", response_model=TemplatePreviewOut)
def preview_template(
    payload: TemplatePreviewIn, _: TemplatesRead
) -> TemplatePreviewOut:
    """Exactly what a patient would receive for these words, and whether they may be published."""
    language = _language(payload.language)
    _patient_template(payload.template, payload.channel)
    text = registry.TemplateText(
        template=payload.template,
        channel=payload.channel,
        language=language,
        version=0,
        body=payload.body.strip(),
        subject=(payload.subject or "").strip() or None,
    )
    try:
        worst = registry.validate(text)
    except registry.TemplateError as exc:
        return TemplatePreviewOut(valid=False, error=str(exc))
    message = registry.render(
        payload.template, payload.channel, text.body, text.subject, registry.SAMPLE
    )
    out = TemplatePreviewOut(valid=True, subject=message.subject, text=message.text)
    if payload.channel is NotificationChannel.SMS and worst is not None:
        sent = to_gsm7(message.text)
        count = count_segments(sent)
        out = out.model_copy(
            update={
                "text": sent,
                "characters": count.units,
                "encoding": count.encoding.value,
                "segments": count.segments,
                "worst_characters": worst.units,
                "worst_segments": worst.segments,
            }
        )
    return out


@router.get("/versions/{version_id}", response_model=TemplateVersionOut)
def get_template_version(
    version_id: str, _: TemplatesRead, db: DbSession
) -> TemplateVersionOut:
    """One stored version: the words a ledger row's ``template_version_id`` names."""
    row = db.get(NotificationTemplateVersion, version_id)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="No such template version."
        )
    return _stored_out(row)


@router.get("/{template}/{channel}/{language}", response_model=TemplateKeyOut)
def get_template(
    template: NotificationTemplate,
    channel: NotificationChannel,
    language: str,
    _: TemplatesRead,
    db: DbSession,
) -> TemplateKeyOut:
    """One template in one channel and language: what is sent now, its blanks, and every stored version."""
    lang = _language(language)
    _patient_template(template, channel)
    return TemplateKeyOut(
        current=_current_out(db, template, channel, lang),
        variables=sorted(registry.allowed(template, channel)),
        versions=[_stored_out(row) for row in _stored(db, template, channel, lang)],
    )


@router.post(
    "/{template}/{channel}/{language}/versions",
    response_model=TemplateVersionOut,
    status_code=status.HTTP_201_CREATED,
)
def publish_template(
    template: NotificationTemplate,
    channel: NotificationChannel,
    language: str,
    payload: TemplateDraftIn,
    _: TemplatesWrite,
    claims: CurrentUser,
    db: DbSession,
) -> TemplateVersionOut:
    """Publish words as the next version; patients receive them from the next message on.

    ``422`` with the reason when the words break the variable contract: a blank the message does not have,
    no ticket number, a web push saying more than the number and clinic, or an SMS longer than one segment.
    """
    lang = _language(language)
    _patient_template(template, channel)
    user = resolve_active_user(db, claims)
    try:
        row = registry.publish(
            db,
            template=template,
            channel=channel,
            language=lang,
            body=payload.body,
            subject=payload.subject,
            author=user.email,
            reviewed_by=payload.reviewed_by,
        )
    except registry.TemplateError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    db.commit()
    return _stored_out(row)
