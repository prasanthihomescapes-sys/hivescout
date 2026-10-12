# 0001 — Open core: what is free forever, what may be paid

**Status:** accepted · **Date:** 2026-10-12

## Context

HiveScout is published as open source so that any team shipping AI-written software can close the user-side
loop. A paid edition is possible later, but which features it carries will be decided by how people actually use
the open tool, not up front.

## Decision

**Open source (Apache-2.0), permanently:**
- the UX contract schema, scaffolding, gate and the Claude-assisted draft
- every static lint rule
- every probe (web now; API, conversational and mobile as they land), the fault catalogue and the deterministic detectors
- reports, Playwright test export and knowledge-base sync
- the Claude Code skill

**Candidates for a paid edition** (in a separate private repository, built only when there is demand):
- hosted run history, trends and team dashboards
- a GitHub App that comments findings on pull requests
- LLM persona judging at scale (many personas × flows, with scoring calibrated against human ratings)
- real-device and device-farm mobile runs
- organisation rule packs and policy enforcement

**Never:** telemetry in the open-source tool, or rules that work in the paid edition only.

## Consequences

Paid features must sit on top of the open findings format (`findings.json`) and must not fork it. The open tool
has to be complete enough on its own to make a team's apps better. Otherwise the open core is only a demo.
