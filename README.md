# mesh-test

Runs the tests the repos in a spangap workspace declare in their `tests.yaml`,
picking the ones that changes could affect. Lives beside those repos.

    ./mesh-test                     smoke tests affected by changes since origin/main
    ./mesh-test --tier nightly      smoke and nightly tests
    ./mesh-test --all               every test in the tier, whatever changed
    ./mesh-test --collect-only -q   list what would run
    ./mesh-test -k supe-host        one test by name
    ./mesh-test -m "hil and lora"   by type or hardware tag

Other options: `--since REF`, `--configs NAME,NAME|all`,
`--firmware VARIANT=NAME`, `--hardware TAG,TAG`, `--out DIR`.
Defaults for configs, hardware and firmware come from `local.yaml`
(start from `local.example.yaml`). Needs pytest and PyYAML.

Each test's output: `out/<run>/<repo>/<test>/output.log`; a failure shows
its last 40 lines.

## tests.yaml

    generated: [<path>, …]                 build output in tracked files: not a change
    not_firmware: [<path>, …]              not in the image: not part of a firmware cache key

    test_configs:
      base: <spangap build args shared by every variant>
      catalogue: {<field>: <value>, …}     extra builds.yaml entry fields
      variants:
        <name>: <extra spangap build args, or empty>

    tests:
      - id: <name>
        type: host | sim | hil
        env: local | spangap | sim-mesh    default local
        tier: smoke | nightly              default smoke
        run: <shell command, from the repo root>
        tools: [<command>, …]              checked when env is local
        straddle_deps: [<straddle>, …]
        paths: [<glob>, …]                 narrows triggers in its own repo
        configs: all | [<variant> | <straddle>:<variant>, …]
        hardware: [<tag>, …]               hil only
        timeout: <seconds>                 default 1800

A test runs when its tier is selected and a change touches its own repo
(within `paths`), anything in `straddle_deps`, or, for sim and hil tests,
anything its straddle or build configuration pulls in. A change to
`spangap/spangap` runs everything. Tests get `MESH_WORKSPACE`, `TEST_OUT`
and, with a variant, `TEST_FIRMWARE`.

A sim test's variant is built with `spangap make-builds` (dependencies not
pulled), cached in `cache/firmware/` by the sources it is built from, and
installed in sim-mesh as `mesh-test-<repo>-<variant>_…`. `--firmware` or
`local.yaml` overrides it.
