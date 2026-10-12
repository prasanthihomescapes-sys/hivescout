# Contributing

```bash
pip install -e ".[probe,dev]"
python -m playwright install chromium
python -m pytest -q          # HIVESCOUT_SKIP_BROWSER=1 skips the real-browser test
ruff check .
```

## Adding a rule

1. Describe the rule by **what the user experiences**, not by what the code does. "Spinner never resolves" is a good title. "Missing AbortController" is not.
2. Add a fixture that triggers it (`examples/` or a `tmp_path` test), and a near-miss that must **not** trigger it.
3. Give the rule a `fix` (what to do) and a `why` (the harm to the user).
4. Prefer false negatives to noisy false positives. A rule people ignore will not change their code.

## Adding a probe fault

Add it to `hivescout/probe/faults.py`, teach `export.py` to reproduce it in Playwright, and extend
`tests/fixtures/site/` so that one page handles the fault well and one handles it badly.
