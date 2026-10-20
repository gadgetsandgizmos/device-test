import hashlib
import json
import os
import platform
import re
import subprocess

import yaml

from mesh_test.changes import git

SPANGAP = "spangap/spangap"
BUILD_TIMEOUT = 3600
TAIL_LINES = 40
ADDED = re.compile(r"^added (\S+)", re.IGNORECASE | re.MULTILINE)
ARCHES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}


class FirmwareError(Exception):
    pass


def entry(variant):
    short = variant.owner.rpartition("/")[2]
    return {**variant.catalogue,
            "name": f"sim-{variant.name}",
            "target": "linux",
            "arch": ARCHES.get(platform.machine(), platform.machine()),
            "base": f"mesh-test-{short}-{variant.name}",
            "invocation": variant.build_args}


def repo_state(ws, repo):
    ignore = ws.generated(repo.name)
    h = hashlib.sha256()
    h.update(git(repo.path, "rev-parse", "HEAD").stdout.encode())
    excludes = [f":(exclude){p}" for p in sorted(ignore)]
    h.update(git(repo.path, "diff", "--binary", "HEAD", "--", ".", *excludes).stdout.encode())
    untracked = git(repo.path, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    for name in sorted(set(untracked) - ignore):
        h.update(name.encode())
        h.update((repo.path / name).read_bytes())
    return h.hexdigest()


def build_repos(ws, variant):
    names = {SPANGAP}
    for root in ws.build_roots(variant):
        names |= ws.closure(root)
    return sorted(n for n in names if n in ws.repos)


def cache_key(ws, variant):
    states = {name: repo_state(ws, ws.repos[name]) for name in build_repos(ws, variant)}
    blob = json.dumps({"entry": entry(variant), "repos": states}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def missing_straddles(ws, variant):
    needed = set()
    for root in ws.build_roots(variant):
        needed |= ws.closure(root, hard_only=True)
    return sorted(n for n in needed if n not in ws.repos)


def _tail(path):
    return "\n".join(path.read_text(errors="replace").splitlines()[-TAIL_LINES:])


def _sim(sim, *args):
    return subprocess.run([str(sim), "firmware", *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


def _install(sim, zip_path, cache_dir):
    done = _sim(sim, "add", str(zip_path))
    m = ADDED.search(done.stdout + done.stderr)
    if done.returncode or not m:
        raise FirmwareError(f"sim firmware add {zip_path.name} failed:\n{done.stdout}{done.stderr}")
    (cache_dir / "name").write_text(m.group(1) + "\n")
    return m.group(1)


def ensure(ws, variant, cache_root, sim):
    cache_dir = cache_root / cache_key(ws, variant)[:16]
    name_file = cache_dir / "name"
    zips = sorted(cache_dir.glob("*.zip")) if cache_dir.is_dir() else []
    if name_file.is_file():
        name = name_file.read_text().strip()
        if name in _sim(sim, "list", name).stdout:
            return name
    if zips:
        return _install(sim, zips[-1], cache_dir)
    missing = missing_straddles(ws, variant)
    if missing:
        raise FirmwareError(f"not in the workspace, so {variant.label} can't be built: "
                            + ", ".join(missing))
    cache_dir.mkdir(parents=True, exist_ok=True)
    catalogue = {"project": "mesh-test", "url": "", "builds": [entry(variant)]}
    (cache_dir / "builds.yaml").write_text(yaml.safe_dump(catalogue, sort_keys=False))
    log = cache_dir / "build.log"
    env = dict(os.environ, SPANGAP_NO_PULL="1")
    remembered = ws.root / ".spangap-build"
    saved = remembered.read_bytes() if remembered.is_file() else None
    with open(log, "wb") as fh:
        try:
            code = subprocess.run(["spangap", "make-builds"], cwd=cache_dir, env=env,
                                  stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                  timeout=BUILD_TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            raise FirmwareError(f"{variant.label}: build timed out after {BUILD_TIMEOUT} s; log: {log}")
        finally:
            if saved is None:
                remembered.unlink(missing_ok=True)
            else:
                remembered.write_bytes(saved)
    zips = sorted(cache_dir.glob("*.zip"))
    if code or not zips:
        raise FirmwareError(f"{variant.label}: build failed (exit {code}); log: {log}\n{_tail(log)}")
    return _install(sim, zips[-1], cache_dir)
