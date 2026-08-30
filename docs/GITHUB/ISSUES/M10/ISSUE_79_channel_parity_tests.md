# Issue 79: Cross-channel parity tests and channel-mix analytics

**Area:** Backend / Quality
**Milestone:** M10 - USSD & WhatsApp Channels
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issues 72–78
**Estimate:** 2 days
**Status:** Planned

## Context

The project's central architectural claim is 'one engine, four doors'. This suite is what makes that
claim testable: the same patient, joining the same queue through four different channels, must end up in
exactly the same state.

## Scope

- Parity suite running the same scenarios through web, USSD, WhatsApp and reception walk-in
- Assertions on resulting ticket state, sequence position, notifications and audit rows
- Channel-mix analytics recording the source of every ticket
- A channel-mix report for the M12 dashboard
- Documented differences where a channel genuinely cannot match, with the reason

## Acceptance criteria

- [ ] All four channels produce identical ticket state for the same scenario
- [ ] Sequence fairness holds across channels, no channel systematically gets a better position
- [ ] Channel mix is reportable per site and per day
- [ ] Every deliberate channel difference is documented and justified
- [ ] The suite runs in CI within the time budget
- [ ] A regression that breaks parity fails the build

## Files touched

- `tests/integration/test_channel_parity.py`
- `app/services/discovery_analytics.py`
- `contracts/channels.yaml`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #79
