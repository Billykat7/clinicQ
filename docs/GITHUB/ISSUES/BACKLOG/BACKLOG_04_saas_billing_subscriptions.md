# Backlog 04: Per-clinic subscriptions and billing

**Area:** Backend / Platform · **Post-capstone** · **Depends on:** M14

## Context

The business plan prices ClinicQ as a per-clinic monthly SaaS fee. For the capstone pilot, billing is
handled manually: one clinic, one invoice, no code. Turning that into a self-service subscription
(plans, trials, payment collection, dunning, per-site entitlements) is real product work that belongs
after the pilot proves clinics will pay at all.

## Rough scope when picked up

- Plans and entitlements gating optional features (channels, appointments, district reporting)
- Payment collection via a South African payment provider, with card and debit-order support
- Trials, upgrades, downgrades, and a grace period before suspension
- Invoices, statements and an operator billing dashboard
