# Backlog 03: Clinic ratings and reviews in discovery

**Area:** Full-stack / Discovery · **Post-capstone** · **Depends on:** Issue 87

## Context

Zocdoc and Solv both make ratings central to discovery, and M11 already collects a post-visit
satisfaction score (Issue 87). Publishing those scores in the discovery list would help patients choose,
and would give clinics a reason to care about their wait times.

## Why it is parked

Public ratings on public health facilities are politically and ethically loaded: a understaffed public
clinic serving the most patients may score worst through no fault of its own, and a visible score could
push demand toward already-comfortable private practices. This needs a deliberate policy position and
probably a conversation with health authorities before it ships, not a sprint.

## Rough scope when picked up

- Aggregate score shown only above a minimum response count
- Separate the things a clinic controls (wait, courtesy) from the things it does not (funding, staffing)
- Right of reply for clinics, and a moderation path for free text
- Explicit policy note on how public and private facilities are presented fairly
