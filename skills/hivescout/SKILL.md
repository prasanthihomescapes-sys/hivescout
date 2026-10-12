---
name: hivescout
description: User-side UX and resilience gate for any app with users — write the UX contract (loading, empty, error, offline, slow states and recovery) BEFORE building a page, endpoint, screen or conversation flow; run `hivescout lint` and `hivescout contract check` before calling work done; probe the running app under injected faults. Use for "new page/screen/endpoint/flow", "add a feature", "is this ready to ship", "graceful failure", "fallback", "empty state", "error state", "offline", "UX check", "/hivescout".
---

# HiveScout: build for the user's side, not just the repo's side

Code that passes its tests can still fail the person using it: endless spinners, blank pages on a 500, mock
numbers during an outage, errors with no way back, empty tables with no guidance. This skill moves those decisions
**before** code and checks them **after**.

## 1. Before building any user-facing flow: write the contract

- Find the contracts folder: `$HIVESCOUT_WORKSPACE/contracts/<project>/` if a workspace is set, else `<app>/hivescout/contracts/`.
- If there are no contracts yet, run `hivescout contract init <app>` (or `--target <name>`). If they exist, run `--update`, which adds flows for new routes.
- For the flow you are about to build, fill in **every** required state. Each one needs `shows` (what the user sees), `recovery` (retry, back, home, reload, contact, sign_in, edit_input, wait) and `copy`.
- Decide `fallback_policy`. **Never** substitute data silently. Cached data must carry a visible notice.
- Show the user the filled states for this flow in one short table **before** writing code. This is where the refactor gets avoided.

## 2. Build to the contract

- Every state in the contract has a code path and a visible UI (or response or reply).
- Next.js: `error.tsx` with a reset button, `loading.tsx` and `not-found.tsx` for the segment. React/Expo: an error boundary.
- Every fetch has a timeout (`AbortSignal.timeout`). Every catch either renders an error state or re-throws.
- APIs: return stable error codes and plain messages, never traces. Handlers must not use undefined names.

## 3. Before saying "done"

```bash
hivescout contract check <app> --fail-on high      # contract complete and in sync with routes
hivescout lint <app> --fail-on high                # no silent-failure patterns
```

If a preview or dev server is running:

```bash
hivescout probe --base-url <url> --route <route> [--faults http500,timeout,offline,empty,malformed]
```

For any flow where the user *does* something (sign in, save, pay, submit), add a journey to its contract flow
(`steps` = setup, `act` = the click that sends; see the README). Probe it with `--journey <id>`. The checks are:
input kept on failure, feedback on click, no double submit, and the user told plus able to retry. Behind a login,
use `hivescout auth --target <name>` (a person signs in) **before** probing, or the report will say the session
is missing. Never put real credentials in a contract: scripted auth reads a test account from `${env:...}`.
Faulted runs never send writes; only use `--allow-mutations` against data you may change.

Then read `report.md` and look at the screenshots of any finding. Fix high findings, or state plainly which ones
remain and why. Do not suppress a rule just to pass the gate. A `hivescout-ignore` comment needs a reason next
to it.

## 4. Close the loop

- `hivescout export-test <run>/findings.json --out <repo>/e2e/hivescout.spec.ts` turns probe findings into regression tests.
- If a lessons KB is configured, run `hivescout kb-sync <run>/findings.json --kb <path/to/kb.py>` to record recurring high findings.
