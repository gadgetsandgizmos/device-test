import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mesh_test import select
from mesh_test.changes import changed_files
from mesh_test.workspace import Workspace

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t"]


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *GIT_ID, *args], check=True, capture_output=True)


def make_repo(root, dirname, straddle=None, tests=None, remote=None):
    path = root / dirname
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    if remote:
        git(path, "remote", "add", "origin", remote)
    if straddle:
        (path / "straddle.yaml").write_text(yaml.safe_dump(straddle))
    if tests:
        (path / "tests.yaml").write_text(yaml.safe_dump(tests))
    (path / "README").write_text("x\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


@pytest.fixture
def ws(tmp_path):
    make_repo(tmp_path, "spangap-core", {"name": "spangap/spangap-core"})
    make_repo(tmp_path, "rns", {"name": "reticulous/rns"})
    make_repo(tmp_path, "iface-lora", {"name": "reticulous/iface-lora", "requires": ["reticulous/rns"]},
              {"tests": [{"id": "host-a", "type": "host", "run": "true", "paths": ["src/**"]}]})
    make_repo(tmp_path, "reticulous",
              {"name": "reticulous/reticulous",
               "additional_installs": ["reticulous/iface-lora", {"install": "reticulous/rns", "when": "x"}]},
              {"test_configs": {"base": "reticulous/reticulous --with spangap/hw-linux",
                                "variants": {"supe": None, "no-supe": "--kconfig X=y"}},
               "tests": [{"id": "smoke", "type": "sim", "run": "true", "configs": "all",
                          "straddle_deps": ["sim-mesh/sim-mesh"]}]})
    make_repo(tmp_path, "hw-linux", {"name": "spangap/hw-linux"})
    make_repo(tmp_path, "sim-mesh", None, {"tests": [{"id": "ether", "run": "true"}]},
              remote="https://github.com/sim-mesh/sim-mesh.git")
    return Workspace(tmp_path)


def test_names_and_closure(ws):
    assert "sim-mesh/sim-mesh" in ws.repos
    assert ws.closure("reticulous/reticulous") == {
        "reticulous/reticulous", "reticulous/iface-lora", "reticulous/rns", "spangap/spangap-core"}


def test_variants(ws):
    repo = ws.repos["reticulous/reticulous"]
    test = ws.tests_file(repo.name)["tests"][0]
    labels = sorted(v.label for v in ws.variants_for(repo, test))
    assert labels == ["no-supe", "supe"]
    lora = ws.repos["reticulous/iface-lora"]
    v, = ws.variants_for(lora, {"id": "t", "configs": ["reticulous/reticulous:no-supe"]})
    assert v.label == "reticulous/reticulous:no-supe"
    assert v.build_args.endswith("--kconfig X=y")
    assert ws.build_roots(v) == ["reticulous/reticulous", "spangap/hw-linux"]


def affected(ws, changes, repo_name, index=0):
    repo = ws.repos[repo_name]
    test = ws.tests_file(repo_name)["tests"][index]
    variant = ws.variants_for(repo, test)[0]
    return select.is_affected(ws, repo, test, variant, changes)


def test_host_test_follows_its_paths(ws):
    assert affected(ws, {"reticulous/iface-lora": {"src/a.cpp"}}, "reticulous/iface-lora")
    assert not affected(ws, {"reticulous/iface-lora": {"README"}}, "reticulous/iface-lora")
    assert not affected(ws, {"reticulous/rns": {"x.cpp"}}, "reticulous/iface-lora")


def test_sim_test_follows_dependencies(ws):
    assert affected(ws, {"reticulous/rns": {"x.cpp"}}, "reticulous/reticulous")
    assert affected(ws, {"spangap/hw-linux": {"x.cpp"}}, "reticulous/reticulous")
    assert affected(ws, {"sim-mesh/sim-mesh": {"sim"}}, "reticulous/reticulous")
    assert not affected(ws, {}, "reticulous/reticulous")


def test_spangap_tool_runs_everything(ws):
    assert affected(ws, {"spangap/spangap": {"spangap"}}, "reticulous/iface-lora")


def test_tier_and_configs():
    assert select.in_tier({"tier": "smoke"}, "smoke")
    assert not select.in_tier({"tier": "nightly"}, "smoke")
    assert select.in_tier({"tier": "nightly"}, "nightly")


def test_changed_files(tmp_path):
    path = make_repo(tmp_path, "r")
    assert changed_files(path, "main") == set()
    (path / "README").write_text("y\n")
    (path / "new.txt").write_text("n\n")
    assert changed_files(path, "main") == {"README", "new.txt"}
    git(path, "commit", "-q", "-am", "edit")
    assert changed_files(path, "HEAD~1") == {"README", "new.txt"}
