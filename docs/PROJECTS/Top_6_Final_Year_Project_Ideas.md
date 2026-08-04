# Final Year Project Ideas (Top 6)

Compact cards for the **Richfield Graduate BSc IT (Distance Learning) Capstone 2026** shortlist.

- Feature modules (browser tabs): [`index.html`](index.html)
- Main repo hub: [`../../README.md`](../../README.md)
- This folder index: [`README.md`](README.md)

## 1. CivicConnect (`civic-connect`)
**Description:** Community platform for reporting municipal issues (potholes, leaks, dumping, streetlights) with GPS, photos, status tracking and analytics.

**Team split**
1. Frontend (React/Next.js)
2. Backend APIs
3. Authentication & Security
4. Maps/GIS
5. Notifications & Admin Dashboard
6. DevOps, Testing & CI/CD

**Monetisation:** SaaS for municipalities, white-label licensing, maintenance contracts.

**Effort:** High · **Complexity:** High

**Architecture:** React → REST API → PostgreSQL/PostGIS → Object Storage → Notification service.

**Tech:** Next.js, Micronaut (or FastAPI), PostgreSQL, PostGIS, Docker, GitHub Actions.

---

## 2. EduAttend (`edu-attend`)
**Description:** School attendance with QR/face recognition, parent notifications and principal dashboards.

**Team split**
1. Teacher UI
2. Parent portal/mobile
3. Backend
4. Face recognition/QR
5. Notifications & Reporting
6. DevOps & QA

**Monetisation:** Subscription per school, education department licensing.

**Effort:** Medium-High · **Complexity:** High

**Tech:** React, Flutter, FastAPI, PostgreSQL, OpenCV, Docker.

---

## 3. KasiMarket (`kasi-market`)
**Description:** Marketplace for township businesses including catalogues, ordering, delivery and analytics.

**Team split**
1. Customer frontend
2. Merchant portal
3. Backend
4. Payments & Orders
5. Analytics/Admin
6. DevOps & Testing

**Monetisation:** Transaction fees, subscriptions, promoted listings.

**Effort:** Medium · **Complexity:** Medium

**Tech:** Next.js, NestJS, PostgreSQL, Redis, Docker.

---

## 4. CommunityNet (`community-net`)
**Description:** ISP/community Wi-Fi management platform for subscribers, payments, bandwidth and captive portal.

**Team split**
1. Customer portal
2. Admin portal
3. Backend
4. Network integration
5. Billing & Reporting
6. DevOps/Monitoring

**Monetisation:** Monthly SaaS, managed services.

**Effort:** High · **Complexity:** High

**Tech:** React, Micronaut, PostgreSQL, FreeRADIUS, MikroTik API, Docker.

---

## 5. ClinicQueue (`clinic-queue`)
**Description:** Digital clinic appointment and queue management with notifications and analytics.

**Team split**
1. Patient app
2. Clinic dashboard
3. Backend
4. Notifications
5. Reporting
6. DevOps & QA

**Monetisation:** Licensing to clinics, hosted SaaS.

**Effort:** Medium · **Complexity:** Medium

**Tech:** Flutter, React, FastAPI, PostgreSQL, Redis, Docker.

---

## 6. IsangoPass (`isango-pass`)
**Description:** Access control and visitor management for offices, campuses, estates and industrial sites: pre-registration, gate check-in, badges, host notifications and audit logs. (*Isango* = gate in isiZulu/isiXhosa.)

**Team split**
1. Visitor / host portal
2. Guard / reception app (kiosk or mobile)
3. Backend
4. Access zones & credentials
5. Notifications & audit reporting
6. DevOps & QA

**Monetisation:** Site license, hosted SaaS.

**Effort:** Medium-High · **Complexity:** Medium-High

**Tech:** React/Next.js, Flutter, FastAPI (or Micronaut), PostgreSQL, Redis, Docker.

---

# Suggested Common Architecture

- Frontend: React (Next.js)
- Mobile: Flutter (where applicable)
- Backend: Micronaut / FastAPI
- Database: PostgreSQL
- Authentication: JWT + RBAC
- Storage: MinIO/S3
- Containers: Docker Compose
- CI/CD: GitHub Actions
- Reverse Proxy: Nginx

# Suggested Timeline (16 Weeks)

| Week | Deliverables |
|------|--------------|
|1|Requirements, scope, roles, Git repositories, architecture|
|2|UI mockups, database design, API contracts|
|3|Authentication, project skeletons, CI/CD|
|4|Core database and CRUD services|
|5|Frontend foundations and navigation|
|6|Primary business workflows|
|7|Secondary features|
|8|Integration sprint|
|9|Notifications, uploads, reporting|
|10|Admin dashboards|
|11|Testing and bug fixing|
|12|Performance improvements|
|13|Security hardening|
|14|User acceptance testing|
|15|Documentation, demo preparation|
|16|Final presentation and contingency fixes|
