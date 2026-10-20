from fnmatch import fnmatch

RUN_EVERYTHING = {"spangap/spangap"}


def matches(files, globs):
    if not globs:
        return bool(files)
    return any(fnmatch(f, g) for f in files for g in globs)


def triggers(ws, repo, test, variant):
    names = set()
    for dep in test.get("straddle_deps") or []:
        names |= ws.closure(dep)
    if test.get("type") in ("sim", "hil"):
        names |= ws.closure(repo.name)
        if variant:
            for root in ws.build_roots(variant):
                names |= ws.closure(root)
    names.discard(repo.name)
    return names


def is_affected(ws, repo, test, variant, changes):
    if any(changes.get(n) for n in RUN_EVERYTHING):
        return True
    if matches(changes.get(repo.name, set()), test.get("paths")):
        return True
    return any(changes.get(n) for n in triggers(ws, repo, test, variant))


def in_tier(test, tier):
    return tier == "nightly" or test.get("tier", "smoke") == "smoke"


def in_configs(variant, wanted):
    if variant is None or not wanted:
        return True
    return variant.label in wanted or f"{variant.owner}:{variant.name}" in wanted
