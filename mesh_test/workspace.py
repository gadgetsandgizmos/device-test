import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FOUNDATION = "spangap/spangap-core"


@dataclass
class Variant:
    owner: str
    name: str
    label: str
    build_args: str
    sim_image: dict = field(default_factory=dict)


@dataclass
class Repo:
    path: Path
    name: str
    deps: list[str] = field(default_factory=list)
    is_straddle: bool = False
    requires: list[str] = field(default_factory=list)


def _remote_name(path):
    url = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"],
                         capture_output=True, text=True).stdout.strip()
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else path.name


def _dep_names(entries):
    names = []
    for e in entries or []:
        if isinstance(e, str):
            names.append(e)
        elif isinstance(e, dict) and "install" in e:
            names.append(e["install"])
    return names


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.repos = {}
        for child in sorted(self.root.iterdir()):
            if not (child / ".git").exists():
                continue
            manifest = child / "straddle.yaml"
            if manifest.is_file():
                data = yaml.safe_load(manifest.read_text()) or {}
                requires = _dep_names(data.get("requires"))
                deps = requires + _dep_names(data.get("additional_installs"))
                repo = Repo(child, data.get("name") or _remote_name(child), deps, True, requires)
            else:
                repo = Repo(child, _remote_name(child))
            self.repos[repo.name] = repo
        self._by_path = {r.path: r for r in self.repos.values()}
        self._tests = {}

    def repo_for(self, path):
        return self._by_path.get(Path(path).resolve())

    def closure(self, name, hard_only=False):
        seen, todo = set(), [name]
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            repo = self.repos.get(n)
            if repo:
                todo.extend(repo.requires if hard_only else repo.deps)
                if repo.is_straddle:
                    todo.append(FOUNDATION)
        return seen

    def generated(self, name):
        return set(self.tests_file(name).get("generated") or [])

    def test_only(self, name):
        return set(self.tests_file(name).get("test_only") or [])

    def tests_file(self, name):
        if name not in self._tests:
            repo = self.repos.get(name)
            path = repo.path / "tests.yaml" if repo else None
            self._tests[name] = (yaml.safe_load(path.read_text()) or {}) if path and path.is_file() else {}
        return self._tests[name]

    def variants(self, owner):
        configs = self.tests_file(owner).get("test_configs") or {}
        base = configs.get("base") or ""
        sim_image = dict(configs.get("sim_image") or {})
        return {name: Variant(owner, name, name, f"{base} {args or ''}".strip(), sim_image)
                for name, args in (configs.get("variants") or {}).items()}

    def variants_for(self, repo, test):
        spec = test.get("configs")
        if not spec:
            return [None]
        if spec == "all":
            return list(self.variants(repo.name).values())
        found = []
        for ref in spec:
            owner, _, name = ref.rpartition(":")
            owner = owner or repo.name
            v = self.variants(owner).get(name)
            if v is None:
                raise ValueError(f"{repo.name}: test {test['id']}: no test config '{ref}'")
            label = name if owner == repo.name else f"{owner}:{name}"
            found.append(Variant(owner, name, label, v.build_args, v.sim_image))
        return found

    def build_roots(self, variant):
        tokens = variant.build_args.split()
        roots = tokens[:1]
        roots += [tokens[i + 1] for i, t in enumerate(tokens[:-1]) if t == "--with"]
        return roots
