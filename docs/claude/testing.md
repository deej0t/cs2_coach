---
tags: [cs2-coach, claude-context]
---
# Testing
← [[CLAUDE]] · Detailnotiz, wird nur bei Bedarf gelesen.

## Testing
- `pytest` (see `pytest.ini`, `requirements-dev.txt`). `test_pages_smoke.py` renders every parameterless GET route plus all export detail pages — it catches missing templates and silently lost features.
- **`test_parser_regression.py` runs the full parse chain against real recorded data.** The other parser tests build synthetic DataFrames: they lock in a fix but could never have *found* it, because they carry the same wrong assumption. Every one of the five metric bugs was found by measuring real demos.
- Demos are 42–331 MB and cannot be committed. `tests/tools/record_demo_fixture.py` records what the parser *reads* from one — every `DemoParser` call plus its result — as parquet (280 KB, `tests/fixtures/demo/`). `tests/replay_parser.py` replays it, so no production code needs a test seam.
- An **unrecorded call raises** instead of returning None — if the parser starts reading a new field, the test says so and asks for a re-record rather than silently computing a metric as zero. Re-record when the fields read change; the expected values then need re-verifying, not just overwriting.
- Verified by sabotage: removing the ADR cap moves damage 1041 → 1299 (+24.8 %), swapping the crosshair median for the mean moves 2.14 → 6.46. Both fail the test.
- **`NotRecorded` is an `AssertionError`, and every parser step catches `Exception`** - an unrecorded call makes the metric silently empty instead of failing. Pin the *count* of a new metric in the regression test, not only its median. The recorder used to turn list columns into text (`"[...]"`); it keeps them as lists since 2026-11-13. Record with `--player deej0t`, otherwise the manifest's player is empty and half the regression tests fail.
- `.gitignore` excludes `*.json` wholesale — the fixture manifest needs the `!tests/fixtures/**/*.json` exception, or the parquet files land without their index.
- Tests that reload `cs2_coach.web.app` with a temporary config **must restore module state in a fixture's `finally`**, otherwise later tests find no vault and fail only when run as a suite.
