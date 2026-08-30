# Backlog 05: Referral hand-off between clinics

**Area:** Backend / Domain · **Post-capstone** · **Depends on:** M6, M11

## Context

Primary-care patients are frequently referred onward: to a district hospital, a specialist clinic or a
dedicated service. Today that hand-off is a paper letter and a fresh queue at the receiving facility. A
referral that arrives as a booked slot at the receiving clinic, with the referring clinic visible, is
the natural extension of the queue engine and echoes what the NHS e-Referral Service does nationally.

## Rough scope when picked up

- Referral record linking origin site, destination site, service and reason category
- Destination-side acceptance queue with capacity awareness
- Patient notification with the booked slot at the receiving facility
- Strict consent and minimal clinical detail: a referral reason category, never a diagnosis
