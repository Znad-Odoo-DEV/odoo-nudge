"""Fill the store repository: one branch per Odoo version, the modules at its root.

    python build_store_repo.py <repo> <branch> <source folder> [--scripts]

The branch is checked out (created empty when new), emptied of everything but
.git, and filled from the source folder: every module, a README taken from the
module's own with its links pointed into the module, the licence, the
requirements and, with --scripts, the porting scripts.

write_readmes() is also what mirror_commits.py calls when a commit changes the
README of the module.
"""
import io
import os
import shutil
import subprocess
import sys

SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak*", "*.log", "*.orig")

LICENSE = """Odoo Proprietary License v1.0

Copyright 2026 Ibrahim Elzenad

This software and associated files (the "Software") may only be used (executed,
modified, executed after modifications) if you have purchased a valid license
from the authors, typically via Odoo Apps, or if you have received a written
agreement from the authors of the Software.

You may develop Odoo modules that use the Software as a library (typically by
depending on it, importing it and using its resources), but without copying any
source code or material from the Software. You may distribute those modules
under the license of your choice, provided that this license is compatible with
the terms of the Odoo Proprietary License (For example: LGPL, MIT, or
proprietary licenses similar to this one).

It is forbidden to publish, distribute, sublicense, or sell copies of the
Software or modified copies of the Software.

The above copyright notice and this permission notice must be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

GITIGNORE = "__pycache__/\n*.py[co]\n*.log\n*.bak*\n*.orig\n"
GITATTRIBUTES = "* text=auto eol=lf\n*.png binary\n*.jpg binary\n*.gz binary\n*.ico binary\n*.woff binary\n*.woff2 binary\n"


def write_readmes(source, repo):
    """The README of the repository is the module's own, its links pointed into the module.

    :param source: the folder that holds the module nudge
    :param repo: the root of the store repository
    :return: the READMEs written
    """
    written = []
    for readme in ("README.md", "README.ar.md"):
        src = os.path.join(source, "nudge", readme)
        if os.path.exists(src):
            text = io.open(src, encoding="utf-8").read()
            text = text.replace("](doc/", "](nudge/doc/").replace("](static/", "](nudge/static/")
            io.open(os.path.join(repo, readme), "w", encoding="utf-8", newline="\n").write(text)
            written.append(readme)
    return written


def main(argv):
    repo, branch, source = argv[1], argv[2], argv[3]
    with_scripts = "--scripts" in argv
    main_tree = os.path.join(os.path.dirname(os.path.abspath(source.rstrip("/\\"))), "odoo-nudge")

    def git(*args):
        return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout

    has_commits = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "-q", "HEAD"],
                                 capture_output=True).returncode == 0
    if git("branch", "--list", branch).strip():
        git("checkout", "-q", branch)
    elif has_commits:
        # Each version has its own history: the branches share no files
        git("checkout", "-q", "--orphan", branch)
    else:
        git("symbolic-ref", "HEAD", "refs/heads/" + branch)

    for name in os.listdir(repo):
        if name == ".git":
            continue
        path = os.path.join(repo, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)

    modules = sorted(name for name in os.listdir(source)
                     if os.path.isfile(os.path.join(source, name, "__manifest__.py")))
    for name in modules:
        shutil.copytree(os.path.join(source, name), os.path.join(repo, name), ignore=SKIP)

    write_readmes(source, repo)

    for name, text in (("LICENSE", LICENSE), (".gitignore", GITIGNORE), (".gitattributes", GITATTRIBUTES)):
        io.open(os.path.join(repo, name), "w", encoding="utf-8", newline="\n").write(text)
    shutil.copy2(os.path.join(main_tree, "requirements.txt"), os.path.join(repo, "requirements.txt"))
    if with_scripts:
        shutil.copytree(os.path.join(main_tree, "scripts"), os.path.join(repo, "scripts"), ignore=SKIP)

    print(branch, "<-", source, ":", ", ".join(modules))


if __name__ == "__main__":
    main(sys.argv)
