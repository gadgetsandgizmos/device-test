import subprocess


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)


def changed_files(path, since):
    files = set()
    if since and _git(path, "rev-parse", "--verify", "--quiet", f"{since}^{{commit}}").returncode == 0:
        files.update(_git(path, "diff", "--name-only", f"{since}...HEAD").stdout.splitlines())
    for line in _git(path, "status", "--porcelain", "--untracked-files=all").stdout.splitlines():
        files.add(line[3:].split(" -> ")[-1].strip('"'))
    return {f for f in files if f}
