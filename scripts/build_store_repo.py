"""Fill the store repository: one branch per Odoo version, the modules at its root.

    python build_store_repo.py <repo> <branch> <source folder> [--scripts]
    python build_store_repo.py <scratch folder> - <source folder> --dry-run
    python build_store_repo.py <repo> <branch> <source folder of a port> --partial   (Odoo 18 and 17 have fewer modules)

With --dry-run the modules are copied into a plain folder (no git, nothing
checked out), to see what would ship and how large it is.

Of the folder static/description of a module only what the page
index.html uses and the manifest lists under "images" is copied (and
icon.png): a recording, a folder of trial screenshots or a draft left there
never reaches the store. The build fails when a module is larger than
MAX_MODULE_BYTES or holds audio or video.

The branch is checked out (created empty when new), emptied of everything but
.git, and filled from the source folder: every module, a README taken from the
module's own with its links pointed into the module, the licence, the
requirements and, with --scripts, the porting scripts.

write_readmes() is also what mirror_commits.py calls when a commit changes the
README of the module.
"""
import ast
import io
import os
import re
import shutil
import subprocess
import sys

SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak*", "*.log", "*.orig")
# What a module may weigh: the largest is nudge, with the bundle of the documentation (2 MB), the full Arabic
# translation (1.5 MB), the skills and the compliance packs (about 9.2 MB in 19.0.1.58.0)
MAX_MODULE_BYTES = 12 * 1024 * 1024
# The modules of the store: nothing else in the source folder ships, and none may be missing
MODULES = ("nudge", "nudge_ai", "nudge_builder", "nudge_builder_connect", "nudge_builder_realestate", "nudge_builder_workshop",
           "nudge_connect", "nudge_discuss", "nudge_documents", "nudge_mail", "nudge_mcp",
           "nudge_messaging", "nudge_spreadsheet", "nudge_studio", "nudge_website")
MEDIA = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".mp4", ".mov", ".webm", ".mkv", ".avi")
REFERENCE = re.compile(r"""(?:src|href|poster)\s*=\s*["']([^"']+)["']|url\(\s*["']?([^"')]+)["']?\s*\)""", re.I)

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

The documentation of Odoo included in nudge/data/odoo_docs.jsonl.gz is not
covered by this license: it is (c) Odoo S.A., licensed CC BY-SA 4.0
(https://github.com/odoo/documentation).
"""

GITIGNORE = "__pycache__/\n*.py[co]\n*.log\n*.bak*\n*.orig\n*.mp3\n*.mp4\n*.wav\n/*/static/description/trial/\n"
GITATTRIBUTES = "* text=auto eol=lf\n*.png binary\n*.jpg binary\n*.gz binary\n*.ico binary\n*.woff binary\n*.woff2 binary\n"


def description_files(module):
    """:return: the files of static/description a store page needs, as paths relative
    to that folder: what index.html refers to, the images of the manifest and the icon

    :param module: the folder of the module
    """
    folder = os.path.join(module, "static", "description")
    keep = {"index.html", "icon.png"}
    page = os.path.join(folder, "index.html")
    if os.path.exists(page):
        for match in REFERENCE.finditer(io.open(page, encoding="utf-8").read()):
            target = (match.group(1) or match.group(2)).split("#")[0].split("?")[0].strip()
            if target and not re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|/)", target, re.I):
                keep.add(os.path.normpath(target).replace("\\", "/"))
    manifest = os.path.join(module, "__manifest__.py")
    for image in ast.literal_eval(io.open(manifest, encoding="utf-8").read()).get("images", []):
        keep.add(os.path.relpath(os.path.join(module, image), folder).replace("\\", "/"))
    return keep


def module_ignore(module):
    """:return: the ``ignore`` of copytree for ``module``: the usual files, and in
    static/description whatever is not in description_files()"""
    folder = os.path.normpath(os.path.join(module, "static", "description"))
    keep = description_files(module)

    def ignore(directory, names):
        skipped = set(SKIP(directory, names))
        here = os.path.normpath(directory)
        if here == folder or here.startswith(folder + os.sep):
            relative = os.path.relpath(here, folder).replace("\\", "/")
            prefix = "" if relative == "." else relative + "/"
            for name in names:
                path = prefix + name
                if not any(wanted == path or wanted.startswith(path + "/") for wanted in keep):
                    skipped.add(name)
        return skipped

    return ignore


def check_modules(repo, modules):
    """Fail when a module weighs more than MAX_MODULE_BYTES or holds audio or video.

    :return: the size of each module, in bytes
    """
    sizes, problems = {}, []
    for name in modules:
        total = 0
        for directory, _dirs, files in os.walk(os.path.join(repo, name)):
            for file in files:
                path = os.path.join(directory, file)
                total += os.path.getsize(path)
                if file.lower().endswith(MEDIA):
                    problems.append(f"{os.path.relpath(path, repo)}: audio or video does not ship")
        sizes[name] = total
        if total > MAX_MODULE_BYTES:
            problems.append(f"{name}: {total / 1048576:.1f} MB is over the limit of {MAX_MODULE_BYTES / 1048576:.0f} MB")
    if problems:
        raise SystemExit("The build is refused:\n  " + "\n  ".join(problems))
    return sizes


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
    dry_run = "--dry-run" in argv
    # The folder above scripts/ holds requirements.txt; an older layout kept it in a sibling "odoo-nudge"
    main_tree = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.exists(os.path.join(main_tree, "requirements.txt")):
        main_tree = os.path.join(os.path.dirname(os.path.abspath(source.rstrip("/\\"))), "odoo-nudge")

    def git(*args):
        return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout

    if dry_run:
        os.makedirs(repo, exist_ok=True)
    else:
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
                     if name in MODULES and os.path.isfile(os.path.join(source, name, "__manifest__.py")))
    if set(MODULES) - set(modules) and "--partial" not in argv:  # --partial: a port of Odoo 18 or 17 leaves out the modules that version cannot run
        raise SystemExit("The build is refused: missing module(s) " + ", ".join(sorted(set(MODULES) - set(modules))))
    for name in modules:
        shutil.copytree(os.path.join(source, name), os.path.join(repo, name),
                        ignore=module_ignore(os.path.join(source, name)))
    sizes = check_modules(repo, modules)

    write_readmes(source, repo)

    for name, text in (("LICENSE", LICENSE), (".gitignore", GITIGNORE), (".gitattributes", GITATTRIBUTES)):
        io.open(os.path.join(repo, name), "w", encoding="utf-8", newline="\n").write(text)
    shutil.copy2(os.path.join(main_tree, "requirements.txt"), os.path.join(repo, "requirements.txt"))
    if with_scripts:
        shutil.copytree(os.path.join(main_tree, "scripts"), os.path.join(repo, "scripts"), ignore=SKIP)

    print(branch, "<-", source, ":", ", ".join(modules))
    for name in modules:
        print(f"  {name:20} {sizes[name] / 1024:9.0f} KB")


if __name__ == "__main__":
    main(sys.argv)
