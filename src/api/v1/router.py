"""Aggregate API v1 routers (one include per bounded context / future microservice).

Kernel routers first, then anything a feature flag turned on, then yours. Adding a module means
adding two lines here — an import and an `include_router` — and nothing else.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.v1.routes.admin import router as admin_router
from src.api.v1.routes.auth import router as auth_router
from src.api.v1.routes.rbac_admin import router as rbac_admin_router
from src.api.v1.routes.reference import router as reference_router
from src.api.v1.routes.webhooks import router as webhooks_router
from src.modules.alerts.router import router as alerts_router
from src.modules.audit.router import router as audit_router
from src.modules.documents.esign_router import router as esign_router
from src.modules.documents.router import router as documents_router
from src.modules.messaging.router import router as messaging_router
from src.modules.notifications.router import router as notifications_router
from src.modules.patients.router import router as patients_router
from src.modules.staff.router import router as staff_router
from src.modules.widgets.router import router as widgets_router

# ── your routers ─────────────────────────────────────────────────────────────
# from src.modules.<name>.router import router as <name>_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)
api_v1_router.include_router(admin_router)
api_v1_router.include_router(rbac_admin_router)
api_v1_router.include_router(audit_router)
api_v1_router.include_router(webhooks_router)
api_v1_router.include_router(reference_router)
api_v1_router.include_router(notifications_router)
api_v1_router.include_router(messaging_router)
api_v1_router.include_router(alerts_router)
api_v1_router.include_router(documents_router)
api_v1_router.include_router(esign_router)
api_v1_router.include_router(widgets_router)
api_v1_router.include_router(patients_router)
api_v1_router.include_router(staff_router)
