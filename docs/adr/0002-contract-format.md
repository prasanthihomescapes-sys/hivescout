# 0002 — UX contracts are YAML per surface, validated by JSON Schema

**Status:** accepted · **Date:** 2026-10-12

## Context

The costly refactors happen when error, empty and offline states are bolted on after a feature is built: data
shapes, components and copy all have to change. Moving that decision before code means the agent writing the
feature needs a format that is cheap to write and that a gate can check mechanically.

## Decision

- One YAML file per surface (`web.yml`, `api.yml`, `convo.yml`, `mobile.yml`) in a contracts folder, one flow per
  route, endpoint or conversation trigger.
- Each surface has a fixed set of **required states** (see README). A state either describes what the user sees
  and how they recover, or it says `not_applicable: <why>`.
- `fallback_policy` is an enum without a "silent" option. Substituting data without telling the user cannot be
  declared at all.
- `contract check` fails on a missing contract (CT000), an invalid schema (CT001), a missing state (CT002), an
  unfilled TODO (CT003), a dead-end recovery (CT004) and code routes with no flow (CT005).

## Alternatives rejected

- **Markdown checklists in the spec:** humans read them, but nothing can gate on them.
- **Annotations in code:** they only exist after the code does, which is too late.
- **Storybook stories per state:** these work for components but not for APIs or conversations, and they need the
  UI to exist first.

## Consequences

Contracts can drift from the code, and CT005 plus `contract init --update` catch new routes. The web probe reads
its routes from the contract, so the design-time and test-time halves share one source.
