"""Fill the store repository: one branch per Odoo version, the modules at its root.

    python build_store_repo.py <repo> <branch> <source folder> [--scripts]

The branch is checked out (created empty when new), emptied of everything but
.git, and filled from the source folder: every module, a README taken from the
module's own with its links pointed into the module, the licence, the
requirements and, with --scripts, the porting scripts.
"""
import io
import os
import shutil
import subprocess
import sys

REPO, BRANCH, SOURCE = sys.argv[1], sys.argv[2], sys.argv[3]
WITH_SCRIPTS = "--scripts" in sys.argv
BOOTCAMP = os.path.dirname(os.path.abspath(SOURCE.rstrip("/\\")))
MAIN = os.path.join(BOOTCAMP, "odoo-nudge")
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


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], check=True, capture_output=True, text=True).stdout


has_commits = subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "-q", "HEAD"],
                             capture_output=True).returncode == 0
if git("branch", "--list", BRANCH).strip():
    git("checkout", "-q", BRANCH)
elif has_commits:
    # Each version has its own history: the branches share no files
    git("checkout", "-q", "--orphan", BRANCH)
else:
    git("symbolic-ref", "HEAD", "refs/heads/" + BRANCH)

for name in os.listdir(REPO):
    if name == ".git":
        continue
    path = os.path.join(REPO, name)
    shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)

modules = sorted(name for name in os.listdir(SOURCE)
                 if os.path.isfile(os.path.join(SOURCE, name, "__manifest__.py")))
for name in modules:
    shutil.copytree(os.path.join(SOURCE, name), os.path.join(REPO, name), ignore=SKIP)

for readme in ("README.md", "README.ar.md"):
    src = os.path.join(SOURCE, "nudge", readme)
    if os.path.exists(src):
        text = io.open(src, encoding="utf-8").read()
        text = text.replace("](doc/", "](nudge/doc/").replace("](static/", "](nudge/static/")
        io.open(os.path.join(REPO, readme), "w", encoding="utf-8", newline="\n").write(text)

for name, text in (("LICENSE", LICENSE), (".gitignore", GITIGNORE), (".gitattributes", GITATTRIBUTES)):
    io.open(os.path.join(REPO, name), "w", encoding="utf-8", newline="\n").write(text)
shutil.copy2(os.path.join(MAIN, "requirements.txt"), os.path.join(REPO, "requirements.txt"))
if WITH_SCRIPTS:
    shutil.copytree(os.path.join(MAIN, "scripts"), os.path.join(REPO, "scripts"), ignore=SKIP)

print(BRANCH, "<-", SOURCE, ":", ", ".join(modules))
