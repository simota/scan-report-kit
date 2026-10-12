#!/usr/bin/env python3
"""Copy scan_report_kit into a skill's scripts directory, or check a copy.

    python3 scripts/vendor.py DEST            # write DEST/scan_report_kit/
    python3 scripts/vendor.py DEST --check    # exit 1 if DEST's copy was edited

Skills are installed by symlinking one directory, so they cannot import from
this repository at run time: each consumer carries a copy. The copy holds a
MANIFEST of SHA-256 digests; --check needs only the copy itself, so a
consumer's CI can verify it without this repository.
"""

import argparse
from contextlib import contextmanager
import hashlib
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

PACKAGE = Path(__file__).resolve().parents[1] / "scan_report_kit"
NAME = "scan_report_kit"
MANIFEST = "MANIFEST"


def _ignored(name):
    return name == "__pycache__" or name.endswith(".pyc")


def _path_problem(rel):
    """MANIFEST paths must name one portable, relative package file."""
    if (not rel or "\\" in rel or PurePosixPath(rel).is_absolute()
            or PureWindowsPath(rel).drive or any(ord(c) < 32 or ord(c) == 127 for c in rel)
            or any(part in ("", ".", "..") for part in rel.split("/"))):
        return "invalid relative path"
    if rel == MANIFEST or any(_ignored(part) for part in rel.split("/")):
        return "reserved or ignored path"
    return None


def _scan(root):
    """List regular package files without following links, including broken links."""
    root = Path(root)
    paths, problems = [], []
    try:
        mode = root.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            return [], [f"{root}: not a regular directory (symlinks are not allowed)"]
    except OSError as e:
        return [], [f"{root}: {e.strerror or e}"]
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    rel = path.relative_to(root).as_posix()
                    mode = entry.stat(follow_symlinks=False).st_mode
                    if stat.S_ISLNK(mode):
                        problems.append(f"{rel}: symlinks are not allowed")
                    elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                        problems.append(f"{rel}: not a regular file or directory")
                    elif _ignored(entry.name):
                        continue
                    elif stat.S_ISDIR(mode):
                        pending.append(path)
                    elif rel != MANIFEST:
                        problem = _path_problem(rel)
                        if problem:
                            problems.append(f"{rel!r}: {problem}")
                        else:
                            paths.append(path)
        except OSError as e:
            problems.append(f"{directory}: {e.strerror or e}")
    return sorted(paths), sorted(problems)


def files(root):
    paths, problems = _scan(root)
    if problems:
        raise ValueError("; ".join(problems))
    return paths


@contextmanager
def _regular_file(path):
    """Check before reading; a replaced file or symlink must never be hashed."""
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{path}: not a regular file (symlinks are not allowed)")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        after = os.fstat(fd)
        if (not stat.S_ISREG(after.st_mode)
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            raise ValueError(f"{path}: file changed while being read")
        with os.fdopen(fd, "rb") as handle:
            fd = None
            yield handle
    finally:
        if fd is not None:
            os.close(fd)


def digest(path):
    result = hashlib.sha256()
    with _regular_file(path) as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            result.update(chunk)
    return result.hexdigest()


def manifest_text(root):
    return "".join(f"{digest(p)}  {p.relative_to(root).as_posix()}\n" for p in files(root))


def check(copy):
    """Problems with copy against its own MANIFEST (empty when intact)."""
    copy = Path(copy)
    paths, problems = _scan(copy)
    # Do not open a MANIFEST through an unsafe root directory.
    try:
        if not stat.S_ISDIR(copy.lstat().st_mode):
            return problems + [f"{copy}: not a regular directory (symlinks are not allowed)"]
    except OSError as e:
        return problems + [f"{copy}: cannot inspect package directory: {e.strerror or e}"]
    listed = copy / MANIFEST
    try:
        with _regular_file(listed) as handle:
            lines = handle.read().decode("utf-8").splitlines()
    except (OSError, ValueError) as e:
        return problems + [f"{listed}: cannot read MANIFEST: {e}"]
    want = {}
    for number, line in enumerate(lines, 1):
        sha, _, rel = line.partition("  ")
        problem = _path_problem(rel)
        if not re.fullmatch(r"[0-9a-fA-F]{64}", sha) or problem:
            problems.append(f"{listed}:{number}: invalid MANIFEST entry ({problem or 'invalid SHA-256'})")
        elif rel in want:
            problems.append(f"{listed}:{number}: duplicate MANIFEST path {rel!r}")
        else:
            want[rel] = sha.lower()
    if not lines:
        problems.append(f"{listed}: empty MANIFEST")
    have = {}
    for path in paths:
        rel = path.relative_to(copy).as_posix()
        try:
            have[rel] = digest(path)
        except (OSError, ValueError) as e:
            problems.append(f"{rel}: cannot read package file: {e}")
    problems += [f"{rel}: edited in the vendored copy" for rel in sorted(want) if rel in have and have[rel] != want[rel]]
    problems += [f"{rel}: missing from the vendored copy" for rel in sorted(set(want) - set(have))]
    problems += [f"{rel}: not part of scan-report-kit" for rel in sorted(set(have) - set(want))]
    return problems


def vendor(dest):
    target = Path(dest) / NAME
    try:
        source, resolved_target = PACKAGE.resolve(), target.resolve()
    except RuntimeError as e:
        raise ValueError(f"cannot resolve vendor paths: {e}") from e
    if (source == resolved_target or source in resolved_target.parents
            or resolved_target in source.parents):
        raise ValueError(f"refusing to vendor {target}: source and destination overlap")
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError(f"refusing to replace {target}: not a directory")
    # Validate source file types before copying. copytree preserves links if a
    # file is changed concurrently; validation of the staged tree rejects them.
    files(PACKAGE)
    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{NAME}.vendor-", dir=target.parent))
    stage, backup, withdrawn = work / "package", work / "previous", work / "uncommitted"
    keep_work = False
    installing = False

    def ignore(directory, names):
        return [name for name in names if _ignored(name)
                or (Path(directory) == PACKAGE and name == MANIFEST)]

    def rollback_move(source, destination):
        try:
            os.replace(source, destination)
        except BaseException:
            # A signal can arrive just after rename succeeds. Finish restoring
            # the other directory in that case, then re-raise the install error.
            if os.path.lexists(source) or not os.path.lexists(destination):
                raise

    try:
        shutil.copytree(PACKAGE, stage, symlinks=True, ignore=ignore)
        (stage / MANIFEST).write_text(manifest_text(stage), encoding="utf-8")
        problems = check(stage)
        if problems:
            raise ValueError("staged package is invalid: " + "; ".join(problems))
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise ValueError(f"refusing to replace {target}: not a directory")
        try:
            if target.exists():
                os.replace(target, backup)
            installing = True
            os.replace(stage, target)
        except BaseException as e:
            try:
                # Directory rename cannot overwrite a nonempty directory.
                # Withdraw a completed install before restoring the old copy;
                # this also restores an originally absent destination.
                if installing and os.path.lexists(target):
                    if os.path.lexists(stage):
                        raise OSError(f"refusing to replace unexpected destination {target}: "
                                      "the staged copy was not installed")
                    rollback_move(target, withdrawn)
                if os.path.lexists(backup):
                    rollback_move(backup, target)
            except BaseException as rollback:
                keep_work = True
                recovery = (f"original copy is preserved at {backup}" if os.path.lexists(backup)
                            else f"destination is preserved at {target}; recovery directory is preserved at {work}")
                raise OSError(f"could not install {target}; {recovery}; "
                              f"could not restore previous state: {rollback or type(rollback).__name__}") from e
            raise
    finally:
        # A failed restore must leave the original copy available for recovery.
        if not keep_work:
            shutil.rmtree(work)
    return target


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("dest", help="directory that receives (or holds) scan_report_kit/")
    p.add_argument("--check", action="store_true", help="verify the copy against its MANIFEST")
    a = p.parse_args(argv)
    try:
        if a.check:
            problems = check(Path(a.dest) / NAME)
            for problem in problems:
                print(f"vendor.py: {problem}", file=sys.stderr)
            if problems:
                print("vendor.py: change scan-report-kit upstream, then re-vendor", file=sys.stderr)
                return 1
            print(f"vendored scan_report_kit intact ({Path(a.dest) / NAME})")
            return 0
        print(f"vendored {vendor(a.dest)}")
    except (OSError, ValueError) as e:
        print(f"vendor.py: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
