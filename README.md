# HiveScout

**Scout your app the way your users will meet it, before they do.**

AI coding agents ship features fast and test them from the inside: unit tests, type checks, builds. Nobody walks
out to the user's side. So the things a person actually hits slip through. A spinner that never ends. A blank
page when the API returns 500. A dashboard that quietly shows mock numbers during an outage. An error with no way
back. A first-time user staring at an empty table.

HiveScout closes that loop at both ends:

| When | What | Command |
|---|---|---|
| **Before code** | A *UX contract* per flow declares what the user sees when it is loading, empty, failing, offline or slow, and how they recover. A gate fails the build while any state is undecided. | `hivescout contract init` / `check` |
| **On every commit** | Static rules find the code paths that hide failures: swallowed errors, mock fallbacks, fetches with no timeout, missing `error.tsx`/`loading.tsx`, error handlers that crash. | `hivescout lint` |
| **Against the running app** | A headless browser opens each route as a real user and injects one fault at a time (500, 401, timeout, offline, malformed JSON, empty data). It then judges what is on screen: blank, endless spinner, raw stack trace, a silent failure, a missing recovery action. | `hivescout probe` |
| **After** | Findings become a Markdown report with screenshots, Playwright regression tests, and entries in your lessons knowledge base. | `report`, `export-test`, `kb-sync` |

Stacks: Next.js (app and pages router), React/Vite, Expo/React Native and Python APIs (FastAPI/Flask/Django).
Probing works on any web app.

## Quick start

```bash
pip install "hivescout[probe] @ git+https://github.com/prasanthihomescapes-sys/hivescout"
python -m playwright install chromium

hivescout lint path/to/app                       # static: what will fail silently?
hivescout contract init path/to/app               # scaffold contracts/web.yml with a TODO for every state
hivescout contract check path/to/app              # the gate: exit 1 while states are undecided
hivescout probe --base-url http://localhost:3000 --route / --route /dashboard
hivescout export-test hivescout-out/<run>/findings.json --out e2e/hivescout.spec.ts
```

Each run writes `findings.json` and `report.md` (with screenshots for probe runs). The exit code is non-zero
when there are findings at or above `--fail-on` (default `high`), so it can be used directly in CI.

## The UX contract

```yaml
version: 1
surface: web
flows:
  - id: dashboard
    route: /dashboard
    goal: See this week's leads and spend
    data: [GET /api/stats]
    fallback_policy: cached_with_notice     # never: silently substitute data
    states:
      loading: { shows: skeleton cards, recovery: wait, max_ms: 3000 }
      empty:   { shows: "No leads yet + connect a channel", recovery: none, copy: "Connect WhatsApp to start receiving leads." }
      error:   { shows: inline message with retry, recovery: retry, copy: "We couldn't load your numbers. Try again." }
      offline: { shows: banner + last saved numbers, recovery: retry }
```

Required states per surface:

| surface | states |
|---|---|
| web, mobile | loading, empty, error, offline |
| api | error, timeout, unauthorized, invalid_input |
| convo | error, unclear_input, timeout |

A state that genuinely cannot happen is written as `not_applicable: <why>`. Writing the reason down is the point.

`hivescout contract draft contracts/web.yml --spec SPEC.md` asks Claude Code (`claude -p`) to propose the copy and
recovery for every TODO. It writes a `.draft.yml` for you to review, and never overwrites the contract.

## Rules

| id | severity | what the user experiences |
|---|---|---|
| NX001 | high | A crash in a route shows a blank or default error screen (no `error.tsx`) |
| NX002 | medium | Frozen page while server data loads (no `loading.tsx`) |
| NX003 | low | Bad links land on a bare 404 |
| NX004 | medium | Pages-router app without `_error`/`500` |
| RX001 / EX001 | high | React / Expo app with no error boundary |
| EX002 | medium | Mobile app that never checks connectivity |
| JS001 | medium | `fetch` with no timeout: endless spinner on a slow API |
| JS002 | high | Error swallowed: no message, no recovery (`low` if commented as intentional) |
| JS003 | high | Failure substituted with mock/sample data |
| JS004 | medium | Raw `error.message` rendered to the user |
| JS005 | medium | Error only logged to the console |
| JS006 | medium | `useQuery`/`useSWR` without error handling |
| PY001 | high | `except: pass`: the caller gets success-shaped output |
| PY002 | high | The error handler itself crashes (undefined name, e.g. `logger`) |
| PY003 | medium | Hardcoded number returned on failure |
| PY004 | high/medium | Stack trace or raw exception text sent to the client |
| PY005 | medium | Bare `except:` |
| WEB001 | high | Blank screen under a fault |
| WEB002 | high | Spinner never resolves on a hung request |
| WEB003 | high | Stack trace or raw error on screen |
| WEB004 | high | Data failed and the user is not told |
| WEB005 | high | Failure is invisible: the page looks healthy (cached/mock data, no notice) |
| WEB006 | medium | Error shown with no way to recover |
| WEB007 | medium | Uncaught exception under a fault |
| WEB008 | medium | No empty state for a first-time user |
| CT000–CT005 | high/medium | Contract missing, invalid, incomplete, has a dead end, or out of date with the code |

To silence a lint finding, add a `// hivescout-ignore: JS001` comment (or `# hivescout-ignore: PY001` in
Python) on the line or on the line above. Project-wide, use `.hivescout.yml`:

```yaml
ignore_rules: [NX003]
ignore_paths: ["scripts/*"]
```

### What the probe cannot see

Faults are injected into **browser** requests that match `--api-pattern` (default `**/api/**`). Data fetched on
the server (React Server Components, `getServerSideProps`) never passes through the browser. The report's
coverage table marks such faults **not exercised**, and those routes are not counted as passing. Lint rules
NX001/NX002 cover that side.

## Workspaces (multiple apps)

Keep the list of your apps outside this repo, in a private folder:

```json
{"targets": [
  {"name": "shop", "path": "C:/code/shop", "base_url": "http://localhost:3000",
   "api_pattern": "**/api/**", "routes": ["/", "/orders"]}
]}
```

```bash
export HIVESCOUT_WORKSPACE=~/my-scout
hivescout targets
hivescout lint --all
hivescout probe --target shop
```

Runs go to `<workspace>/runs/<target>/<time>-<channel>/`. The latest report per target is kept in
`<workspace>/reports/`.

## With Claude Code

`skills/hivescout/SKILL.md` is a Claude Code skill. With it, the agent writes the contract before it builds a
flow, runs `lint` and `contract check` before it calls the work done, and probes the running app when a preview
is available. To install it, copy the folder to `~/.claude/skills/hivescout/`.

## Open core

Everything in this repository is Apache-2.0 and stays that way: the contracts, every rule, all probes and faults,
reports, test export and the skill. A future hosted edition may add a run history and trend dashboard, PR review
comments, LLM persona judging at scale, and real-device mobile runs. See
[docs/adr/0001-open-core-boundary.md](docs/adr/0001-open-core-boundary.md). There is no telemetry.

## Roadmap

- API probe (timeouts, error payload quality, degraded dependencies) for FastAPI and others
- Conversational probe: replay WhatsApp/Twilio webhooks and flag dead ends and unclear replies
- Mobile probe with Maestro flows on an Android emulator
- Personas beyond viewport: impatient (double-submit, back mid-flow), keyboard-only, screen reader (axe-core)
- Optional LLM judge: "would a non-technical user know what happened and what to do next?"

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every new rule needs a failing fixture and a passing one.
