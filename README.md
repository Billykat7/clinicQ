# Capstone 2026

**Richfield Graduate Institute of Technology** · BSc IT (Distance Learning) · Capstone Project 2026

This is the **main repository** for our final-year capstone. We are currently comparing a shortlist of project ideas. After the team chooses **one** project, this repo will be updated with that project's name, scope, architecture, and implementation.

## Project shortlist

Browse ideas and features here.

> **Features (HTML):** GitHub shows `.html` as source, so the Features column uses a live preview URL that opens the tabbed page and selects the right project. Locally you can also open [`docs/PROJECTS/index.html`](docs/PROJECTS/index.html) in a browser.

| # | Project | Repo slug | Idea card | Features (HTML) |
|---|---|---|---|---|
| 1 | **CivicConnect** | `civic-connect` | [Card](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#1-civicconnect-civic-connect) | [Modules](https://raw.githack.com/Billykat7/rgit-capstone2026/main/docs/PROJECTS/index.html#civic-connect) |
| 2 | **EduAttend** | `edu-attend` | [Card](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#2-eduattend-edu-attend) | [Modules](https://raw.githack.com/Billykat7/rgit-capstone2026/main/docs/PROJECTS/index.html#edu-attend) |
| 3 | **KasiMarket** | `kasi-market` | [Card](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#3-kasimarket-kasi-market) | [Modules](https://raw.githack.com/Billykat7/rgit-capstone2026/main/docs/PROJECTS/index.html#kasi-market) |
| 4 | **CommunityNet** | `community-net` | [Card](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#4-communitynet-community-net) | [Modules](https://raw.githack.com/Billykat7/rgit-capstone2026/main/docs/PROJECTS/index.html#community-net) |
| 5 | **ClinicQueue** | `clinic-queue` | [Card](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#5-clinicqueue-clinic-queue) | [Modules](https://raw.githack.com/Billykat7/rgit-capstone2026/main/docs/PROJECTS/index.html#clinic-queue) |
| 6 | **IsangoPass** | `isango-pass` | [Card](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#6-isangopass-isango-pass) | [Modules](https://raw.githack.com/Billykat7/rgit-capstone2026/main/docs/PROJECTS/index.html#isango-pass) |

**Docs folder:** [`docs/PROJECTS/`](docs/PROJECTS/) · [`Ideas overview`](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md) · [Feature browser (live)](https://raw.githack.com/Billykat7/rgit-capstone2026/main/docs/PROJECTS/index.html) · [Feature browser (repo file)](docs/PROJECTS/index.html)

### At a glance

1. **[CivicConnect](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#1-civicconnect-civic-connect)** - Municipal issue reporting with GPS, photos, status tracking, and analytics.
2. **[EduAttend](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#2-eduattend-edu-attend)** - School attendance (QR/face), parent notifications, and principal dashboards.
3. **[KasiMarket](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#3-kasimarket-kasi-market)** - Township business marketplace: catalogues, ordering, delivery, analytics.
4. **[CommunityNet](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#4-communitynet-community-net)** - Community Wi-Fi / ISP management: subscribers, billing, captive portal.
5. **[ClinicQueue](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#5-clinicqueue-clinic-queue)** - Clinic appointments and digital queue management with notifications.
6. **[IsangoPass](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#6-isangopass-isango-pass)** - Access control and visitor management (*isango* = gate).

---

## Team

| Name | Role (draft) | GitHub Handle |
|---|---|---|
| | Backend Lead | |
| | Frontend/Mobile Dev | |
| | Frontend/Mobile Dev | |
| | Data/ML Engineer | |
| | DevOps/QA Lead | |
| | Docs/UX/Research Lead | |

*(Roles are a starting split - adjust once we scope the chosen project.)*

## Status

**Phase:** Project shortlist / selection

- [ ] Idea finalised (choose 1 from the shortlist)
- [ ] Requirements gathered
- [ ] API/data contract defined
- [ ] Architecture agreed
- [ ] Repo structure set up for chosen project
- [ ] CI/CD pipeline running
- [ ] MVP complete
- [ ] User testing done
- [ ] Demo-ready

**Chosen project:** TBD

## After we choose

Once one idea is selected, we will:

1. Update this README with the project name, problem statement, and stack
2. Flesh out requirements and API contracts under `docs/`
3. Add `frontend/`, `backend/`, and related implementation folders
4. Keep the shortlist docs under `docs/PROJECTS/` for reference

## Suggested common architecture

| Layer | Tool/Framework |
|---|---|
| Frontend | React (Next.js) |
| Mobile | Flutter (where applicable) |
| Backend | Spring Boot / FastAPI |
| Database | PostgreSQL (+ PostGIS where needed) |
| Authentication | JWT + RBAC |
| Storage | MinIO / S3 |
| Containers | Docker Compose |
| CI/CD | GitHub Actions |
| Reverse Proxy | Nginx |

*(Confirm when a single project is chosen.)*

## Planned repo structure

```
/frontend       # client app (web/mobile)
/backend        # API server
/data           # datasets, notebooks, ML models (if applicable)
/docs           # research, meeting notes, diagrams, project ideas
/infra          # CI/CD configs, deployment scripts
README.md
```

## Team workflow

To avoid blocking each other over a 4-month timeline, split work by **layer**, not by feature:

1. **Week 1-2:** Agree on the API contract and data model *before* implementation. Frontend and backend can then build in parallel against mocks.
2. **Backend/API** and **Frontend/Mobile** integrate incrementally, not in one big-bang merge.
3. **Data/ML** (if applicable) stays on a separate track until validated.
4. **DevOps/QA** sets up CI/CD and testing from day one.
5. **Docs/UX/Research** runs continuously (research, notes, report, demo prep).

**Branching:** `main` (stable/demo-ready) ← `dev` (integration) ← feature branches (`feature/xyz`)
**Meetings:** Weekly check-in, day/time TBD.
**Task tracking:** GitHub Projects / Issues (or Trello, Notion, etc.)

## Getting started

```bash
git clone <repo-url>
cd rgit-capstone2026
# Implementation setup TBD once the project is chosen
```

Open the feature overview locally:

```bash
open docs/PROJECTS/index.html
# or: xdg-open docs/PROJECTS/index.html
```

Or use the live preview (same as README Features links):  
https://raw.githack.com/Billykat7/rgit-capstone2026/main/docs/PROJECTS/index.html
## Timeline (draft, 16 weeks)

| Weeks | Focus |
|---|---|
| 1-2 | Problem definition, requirements, API/data contract, repo & tooling setup |
| 3-8 | Parallel development against mocked contracts, weekly integration check-ins |
| 9-12 | Real integration, end-to-end testing, cross-team bug fixing |
| 13-16 | Polish, user testing, report writing, demo rehearsal |

See week-by-week deliverables in [`docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md`](docs/PROJECTS/Top_6_Final_Year_Project_Ideas.md#suggested-timeline-16-weeks).

## License

MIT - see [`LICENSE`](LICENSE).
