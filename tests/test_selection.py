import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mesh_test import firmware, select
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
              {"generated": ["gen.json"], "not_firmware": ["tests/**", "tests.yaml"],
               "test_configs": {"base": "reticulous/reticulous --with spangap/hw-linux",
                                "catalogue": {"category": "reticulum", "driver": "d.py"},
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


def no_supe(ws):
    return ws.variants("reticulous/reticulous")["no-supe"]


def test_catalogue_entry(ws):
    e = firmware.entry(no_supe(ws))
    assert e["category"] == "reticulum" and e["driver"] == "d.py"
    assert e["target"] == "linux" and e["base"] == "mesh-test-reticulous-no-supe"
    assert e["invocation"] == "reticulous/reticulous --with spangap/hw-linux --kconfig X=y"


def test_cache_key_follows_sources_not_generated_files(ws):
    reticulous = ws.repos["reticulous/reticulous"].path
    (reticulous / "gen.json").write_text("{}\n")
    git(reticulous, "add", "gen.json")
    git(reticulous, "commit", "-q", "-m", "gen")
    before = firmware.cache_key(ws, no_supe(ws))
    (reticulous / "gen.json").write_text('{"a": 1}\n')
    assert firmware.cache_key(ws, no_supe(ws)) == before
    assert changed_files(reticulous, "HEAD", ws.generated("reticulous/reticulous")) == set()
    (ws.repos["reticulous/rns"].path / "x.cpp").write_text("x\n")
    assert firmware.cache_key(ws, no_supe(ws)) != before
    assert firmware.cache_key(ws, ws.variants("reticulous/reticulous")["supe"]) != before


def test_cache_key_ignores_commits_and_tests(ws):
    reticulous = ws.repos["reticulous/reticulous"].path
    before = firmware.cache_key(ws, no_supe(ws))
    (reticulous / "tests").mkdir()
    (reticulous / "tests" / "smoke.py").write_text("print(1)\n")
    assert firmware.cache_key(ws, no_supe(ws)) == before
    git(reticulous, "add", "-A")
    git(reticulous, "commit", "-q", "-m", "a test")
    assert firmware.cache_key(ws, no_supe(ws)) == before
    git(reticulous, "commit", "-q", "--allow-empty", "-m", "nothing")
    assert firmware.cache_key(ws, no_supe(ws)) == before
    (reticulous / "README").write_text("changed\n")
    changed = firmware.cache_key(ws, no_supe(ws))
    assert changed != before
    git(reticulous, "commit", "-q", "-am", "readme")
    assert firmware.cache_key(ws, no_supe(ws)) == changed
    assert git_status(reticulous) == ""


def git_status(path):
    return subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                          capture_output=True, text=True).stdout


def test_missing_hard_dependency(tmp_path):
    make_repo(tmp_path, "app", {"name": "o/app", "requires": ["o/gone"], "additional_installs": ["o/soft"]},
              {"test_configs": {"base": "o/app", "variants": {"v": None}}})
    ws = Workspace(tmp_path)
    v = ws.variants("o/app")["v"]
    assert firmware.missing_straddles(ws, v) == ["o/gone", "spangap/spangap-core"]


def test_changed_files(tmp_path):
    path = make_repo(tmp_path, "r")
    assert changed_files(path, "main") == set()
    (path / "README").write_text("y\n")
    (path / "new.txt").write_text("n\n")
    assert changed_files(path, "main") == {"README", "new.txt"}
    git(path, "commit", "-q", "-am", "edit")
    assert changed_files(path, "HEAD~1") == {"README", "new.txt"}
