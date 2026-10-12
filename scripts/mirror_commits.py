"""Replay commits of the SSC worktree on the store repo, one commit for one commit.

    python mirror_commits.py <worktree> <store repo> <branch> <commit> [<commit> ...]

Only files under addons/<module>/ of the modules the store holds are carried,
written with LF endings; the message is the same. Nothing names anyone but
the committer configured in the store repo. A commit that changes the README
of nudge writes the README at the top of the repository again, in the same
commit, as build_store_repo.py writes it.
"""
import importlib.util
import os
import subprocess
import sys

WORKTREE, STORE, BRANCH = sys.argv[1], sys.argv[2], sys.argv[3]
COMMITS = sys.argv[4:]
MODULES = {"nudge", "nudge_ai", "nudge_spreadsheet", "nudge_documents", "nudge_studio", "nudge_mail", "nudge_discuss"}
READMES = {"addons/nudge/README.md", "addons/nudge/README.ar.md"}

# build_store_repo.py sits next to this script; loaded by its path, which python -I allows too
_spec = importlib.util.spec_from_file_location(
    "build_store_repo", os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_store_repo.py"))
build_store_repo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_store_repo)


def git(repo, *args, binary=False):
    out = subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)
    return out.stdout if binary else out.stdout.decode("utf-8")


git(STORE, "checkout", "-q", BRANCH)
for commit in COMMITS:
    message = git(WORKTREE, "log", "-1", "--format=%B", commit).strip()
    changed = git(WORKTREE, "diff-tree", "--no-commit-id", "--name-status", "-r", commit).splitlines()
    touched = 0
    readme_changed = False
    for line in changed:
        status, path = line.split("\t", 1)
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "addons" or parts[1] not in MODULES:
            continue
        target = os.path.join(STORE, *parts[1:])
        readme_changed = readme_changed or path in READMES
        if status.startswith("D"):
            if os.path.exists(target):
                os.remove(target)
                touched += 1
            continue
        data = git(WORKTREE, "show", f"{commit}:{path}", binary=True)
        if not path.endswith((".png", ".gz", ".jpg", ".ico", ".woff", ".woff2")):
            data = data.replace(b"\r\n", b"\n")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
        touched += 1
    if not touched:
        print("skip", commit, "(nothing of the store)")
        continue
    if readme_changed:
        # The modules sit at the root of the store: it is the source and the repository
        build_store_repo.write_readmes(STORE, STORE)
    git(STORE, "add", "-A")
    if not git(STORE, "status", "--porcelain").strip():
        print("skip", commit, "(already there)")
        continue
    git(STORE, "commit", "-q", "-m", message)
    print(git(STORE, "log", "-1", "--format=%h %s").strip()[:90])
