# mesh-test

Test runner for the tests that repos in a spangap workspace declare in their `tests.yaml`.
Only runs tests that could be affected by current changes, and are allowed by current bench hardware.

## Usage

    ./mesh-test [options]

Every option is optional. With none, it runs the smoke tests that changes
since `origin/main` (committed or not) could affect.

| Option | What it does | Default |
|---|---|---|
| `--tier <smoke\|extended>` | which tests are eligible; extended includes smoke | `smoke` |
| `--all` | ignore changes: run every eligible test | off |
| `-k <id>` | runs tests in dependencies in the given tier whose id contains `<id>` | |
| `--since <ref>` | count changes since this commit, in each repo | `origin/main` |
| `--configs <variant>[,<variant>…]` or `all` | which build variants to run | `local.yaml`, else all |
| `--firmware <variant>=<image>` | use this installed image instead of building | `local.yaml` |
| `--hardware <tag>[,<tag>…]` | testable hardware connected to this machine | `local.yaml` |
| `--out <dir>` | output folder | `out/<run>` |
| `--html <path>` | report | `out/<run>/report.html` |
| `--refresh` | reinstall `.venv` (automatic on first run and when `requirements.txt` changes) | |

Test ids are the `id:`s in each repo's
`tests.yaml`; `./mesh-test --collect-only -q` lists them.
Variants are the names under `test_configs: variants:` in a repo's `tests.yaml` (e.g. supe / no-supe in reticulous).
`local.yaml` holds per-machine defaults; start from `local.example.yaml`.

## Examples

| To… | Run |
|---|---|
| run smoke tests in modules that are affected by your changes | `./mesh-test` |
| include the longer tests | `./mesh-test --tier extended` |
| run everything, including tests from modules that don't depend on your changes | `./mesh-test --tier extended --all` |
| run one test, e.g. `supe-host` | `./mesh-test --all -k supe-host` |
| see which tests would run, and their ids | `./mesh-test --collect-only -q` |

## What a run does

1. The launcher sets up `.venv` if it's missing or out of date, restarts
   itself in it, collects every `*/tests.yaml` in
   the workspace (`MESH_WORKSPACE`, else mesh-test's parent) and runs pytest
   with the mesh-test plugin.
2. Each test becomes one pytest test. A test with `configs:` becomes one per
   variant.
3. Tests are dropped if they're outside the chosen tier or variants, or,
   unless `--all` is given, if none of your changes could affect them.
4. Each test: a hil test without matching `--hardware`, or one missing its
   `tools`, is skipped and flagged. Then `run:` runs from the repo root in its `env:`: `local` in
   bash, `spangap` through `spangap docker`, `sim-mesh` through `sim test
   <id>`, within its timeout.
5. Each test's output is saved to `out/<run>/<repo>/<test>/output.log`, and a
   failure shows its last 40 lines. At the end, the run writes
   `out/<run>/report.html`.

## Firmware for sim tests

A variant's image is cached in `cache/firmware/`, keyed by a hash of the
files that go into it (git tree IDs, minus `generated:` and `test_only:`
paths) and its build arguments. On a miss it is built with
`spangap make-builds` (dependencies not pulled, `.spangap-build` restored)
and installed in sim-mesh as `mesh-test-<repo>-<variant>_…`. `--firmware`
or `local.yaml` overrides it.

## tests.yaml

    generated: [<path>, …]                 do not trigger tests based on changes to generated files
    test_only: [<path>, …]                 do not trigger image rebuilds based on changes to tests

    test_configs:
      base: <spangap build args shared by every variant>
      sim_image: {<field>: <value>, …}     how sim-mesh runs the image (see below)
      variants:
        <name>: <extra spangap build args, or empty>

    tests:
      - id: <name>
        type: host | sim | hil
        env: local | spangap | sim-mesh    default local
        tier: smoke | extended             default smoke
        run: <shell command, from the repo root>
        tools: [<command>, …]              commands that must be installed; only checked for `env: local`
        straddle_deps: [<straddle>, …]     straddles outside its own whose changes should also trigger it
        paths: [<glob>, …]                 only changes to these files in the test's own repo trigger it
        configs: all | [<variant> | <straddle>:<variant>, …]   which variants to run it with; all = every variant
        hardware: [<tag>, …]               hardware tags the test needs (hil tests only)
        timeout: <seconds>                 default 1800

A test runs when its tier is selected and a change touches its own repo
(within `paths`), anything in `straddle_deps`, or, for sim and hil tests,
anything its straddle or build configuration pulls in. A change to
`spangap/spangap` runs everything. Tests get `MESH_WORKSPACE`, `TEST_OUT`
and, with a variant, `TEST_FIRMWARE`.

`sim_image:` is only needed by projects whose sim tests build firmware. It
tells sim-mesh how to run the image: its driver, mesh category, virtual
board and radio. Copy the values from the project's `hw-sim-mesh` entry in
its own spangap catalogue.

`generated:` exists only because spangap rewrites reticulous's tracked
`web-interface/package.json` and `package-lock.json` during builds. If that
is cleaned up so builds no longer change tracked files, `generated:` can be
removed.
