# 09 - Pricing, startup budget & break-even

Planning currency: **South African Rand (R)**, mirroring ElimuKadi and
UmojaNet; USD shown for reference at an indicative **R18/$1** (confirm
live rate).

## Pricing model

| Line item | Price | Notes |
|-----------|-------|-------|
| Per-clinic SaaS subscription (discovery listing + queue + dashboard + display) | **R450-1,800/month** | Tiered by clinic size/queue volume (see table below) |
| USSD session cost pass-through | **~R0.20-0.60/session** at cost | USSD gateway providers (Africa's Talking-class) charge per session; pass through or absorb into subscription for Phase 0 |
| WhatsApp conversation cost pass-through | **~R0.30-0.90/conversation** at cost | Meta charges per business-initiated conversation category; mostly avoided by using user-initiated sessions where possible |
| SMS fallback notification | **~R0.25-0.45/SMS** at cost | Only triggered when push/WhatsApp isn't available - see [06](06-channels-app-ussd-whatsapp-web.md) |
| Setup/onboarding (one-off) | **R500-1,500/clinic** | Configuring clinic profile, hours, display mode, staff accounts |
| Support & maintenance | Included in subscription for Phase 0-1; SLA add-on later | Faster response once a clinic is paying for uptime-critical queue management |

### Subscription tiers (per clinic, per month)

| Tier | Typical daily tickets | Price/month | Notes |
|------|---------------------------|-------------|-------|
| Starter | up to 40/day | **R450-700** | Single queue, number-only display, self-serve support |
| Standard | 41-120/day | **R900-1,400** | Multi-room queues, name-lite display option, priority support |
| Plus | 121-300+/day | **R1,600-1,800** | Full reporting suite, USSD+WhatsApp channels included, dedicated support |
| Multi-site / network | Several clinics under one operator (e.g. a group practice or a district) | Custom, volume discount **10-20%** | One combined reporting dashboard across sites |

## Startup budget (one pilot clinic)

| Stage | Budget (R) | Budget (~USD) | Covers |
|-------|------------|-----------------|--------|
| Phase 0 pilot (1 clinic, reusing existing screen/PC) | **R1,800 - 4,000** | **$100 - 220** | Signage box (Pi), UPS, setup labour |
| Phase 0 pilot (1 clinic, no existing screen/PC) | **R6,000 - 15,000** | **$330 - 830** | + TV/monitor, reused/cheap PC |
| District rollout (10+ clinics) | **R25,000 - 70,000** | **$1,400 - 3,900** | Only after Phase 0-1 proves the model - see [12-upscaling-24-months.md](12-upscaling-24-months.md) |

This is **dramatically cheaper to start** than ElimuKadi (card stock +
NFC readers) or UmojaNet (Starlink + radios) - the entire capex is one
small always-on box and maybe a screen.

## Break-even sketch (one clinic)

To cover a lean **~R400-600/month** opex per pilot clinic (hosting share, USSD/WhatsApp/SMS pass-through,
support time), a clinic needs roughly:

- **~1 Standard-tier clinic**, or
- **~2 Starter-tier clinics**, whichever the sales motion favours first (see
  [11-marketing.md](11-marketing.md)).

Because there is no card issuance revenue line to fund Phase 0 hardware (as ElimuKadi has), ClinicQ's
break-even is **purely subscription-driven and reached faster in absolute Rand terms**, even though the
per-clinic ticket is smaller than a per-school ticket.

## Worked example - one Standard-tier clinic

| Price/month | Opex (hosting, gateway pass-through, support) | Net |
|----------------|--------------------------------------------------|-----|
| R900 | R500 | **+R400** |
| R1,200 | R500 | **+R700** |
| R1,400 | R550 | **+R850** |

## Month-by-month revenue projection (single-clinic cohort)

| Month | Clinics live | Revenue (R) | Opex (R) | Net (R) | Cumulative (R) |
|-------|-----------------|---------------|-----------|---------|-------------------|
| 1 | 1 (free/discounted pilot) | 0 | 600 | -600 | -600 |
| 2 | 1 (paid begins) | 700 | 550 | +150 | -450 |
| 3 | 2 | 1,600 | 900 | +700 | +250 |
| 4 | 3 | 2,700 | 1,300 | +1,400 | +1,650 |
| 6 | 5 | 5,000 | 2,200 | +2,800 | +7,050 |
| 12 | 12 | 13,000 | 5,000 | +8,000 | ~+45,000 |
| 18 | 20 | 22,000 | 8,000 | +14,000 | growth funded |
| 24 | 35 | 40,000 | 14,000 | +26,000 | mature pocket |

See the fuller 24-month narrative and growth gates in
[12-upscaling-24-months.md](12-upscaling-24-months.md).

Markdown: this is [09-pricing-and-budget.md](09-pricing-and-budget.md).
