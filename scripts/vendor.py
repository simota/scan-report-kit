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
from contextlib import ExitStack, contextmanager
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
# Resolve every component through held directory descriptors. A path-only
# fallback would reintroduce ancestor-symlink races, so fail closed without
# these APIs (provided by the supported Linux and macOS Python versions).
_SAFE_DIR_FDS = (os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
                 and os.stat in os.supports_follow_symlinks and os.scandir in os.supports_fd
                 and bool(getattr(os, "O_NOFOLLOW", 0)) and bool(getattr(os, "O_DIRECTORY", 0)))


def _require_dir_fds():
    if not _SAFE_DIR_FDS:
        raise ValueError("secure directory-relative verification is unavailable on this platform "
                         "(requires dir_fd, scandir(fd), O_DIRECTORY and O_NOFOLLOW)")


def _identity(info):
    return stat.S_IFMT(info.st_mode), info.st_dev, info.st_ino


def _check_identity(path, current, expected):
    if _identity(current) != _identity(expected):
        raise ValueError(f"{path}: entry changed during verification (symlinks are not allowed)")


def _check_binding(path, expected, *, dir_fd=None):
    _check_identity(path, os.stat(path, dir_fd=dir_fd, follow_symlinks=False), expected)


def _recorded_info(recorded, parts):
    if recorded is None:
        return None
    try:
        return recorded[parts]
    except KeyError:
        raise ValueError(f"{'/'.join(parts)}: entry was not recorded while scanning") from None


def _record_identity(recorded, parts, info):
    # Never replace the first observation, even if scandir yields a name twice.
    _check_identity('/'.join(parts) or '.', info, recorded.setdefault(parts, info))


@contextmanager
def _directory_fd(path, *, dir_fd=None, expected=None):
    """Pin one directory without following a substituted final component."""
    _require_dir_fds()
    before = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    if expected is not None:
        _check_identity(path, before, expected)
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError(f"{path}: not a regular directory (symlinks are not allowed)")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        opened = os.fstat(fd)
        if _identity(before) != _identity(opened):
            raise ValueError(f"{path}: directory changed while being opened")
        yield fd
        # Reads use the pinned descriptor even if the name is replaced. Still
        # reject that replacement instead of certifying the old detached tree.
        _check_binding(path, opened, dir_fd=dir_fd)
    finally:
        os.close(fd)


@contextmanager
def _directory_parts(root_fd, parts, *, recorded=None):
    """Resolve ancestors one at a time; O_NOFOLLOW alone protects only a leaf."""
    with ExitStack() as stack:
        directory = root_fd
        prefix = ()
        for part in parts:
            prefix += (part,)
            directory = stack.enter_context(_directory_fd(part, dir_fd=directory,
                                                           expected=_recorded_info(recorded, prefix)))
        yield directory


def _verify_recorded(root, root_fd, recorded):
    """Recheck every managed entry, including empty dirs and files already read."""
    problems = []
    for parts, expected in sorted(recorded.items()):
        path = root.joinpath(*parts)
        try:
            if not parts:
                _check_binding(root, expected)
            else:
                with _directory_parts(root_fd, parts[:-1], recorded=recorded) as directory:
                    _check_binding(parts[-1], expected, dir_fd=directory)
        except (OSError, ValueError) as e:
            problems.append(f"{path}: {e}")
    return problems


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


def _scan(root, *, root_fd=None, recorded=None):
    """List regular package files without following links, including broken links."""
    root = Path(root)
    if recorded is None:
        recorded = {}
    try:
        if root_fd is None:
            with _directory_fd(root) as opened:
                paths, problems = _scan_opened(root, opened, recorded)
                return paths, problems + _verify_recorded(root, opened, recorded)
        return _scan_opened(root, root_fd, recorded)
    except (OSError, ValueError) as e:
        return [], [f"{root}: {e}"]


def _scan_opened(root, root_fd, recorded):
    paths, problems = [], []
    _record_identity(recorded, (), os.fstat(root_fd))
    pending = [()]
    while pending:
        parts = pending.pop()
        directory = root.joinpath(*parts)
        try:
            with _directory_parts(root_fd, parts, recorded=recorded) as fd:
                with os.scandir(fd) as entries:
                    for entry in entries:
                        path = directory / entry.name
                        rel = path.relative_to(root).as_posix()
                        info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                        mode = info.st_mode
                        if stat.S_ISLNK(mode):
                            problems.append(f"{rel}: symlinks are not allowed")
                        elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                            problems.append(f"{rel}: not a regular file or directory")
                        elif _ignored(entry.name):
                            continue
                        else:
                            entry_parts = parts + (entry.name,)
                            _record_identity(recorded, entry_parts, info)
                            if stat.S_ISDIR(mode):
                                pending.append(entry_parts)
                            elif rel != MANIFEST:
                                problem = _path_problem(rel)
                                if problem:
                                    problems.append(f"{rel!r}: {problem}")
                                else:
                                    paths.append(path)
        except (OSError, ValueError) as e:
            problems.append(f"{directory}: {e}")
    return sorted(paths), sorted(problems)


def files(root):
    paths, problems = _scan(root)
    if problems:
        raise ValueError("; ".join(problems))
    return paths


@contextmanager
def _regular_file(path, *, dir_fd=None, expected=None):
    """Check before reading; a replaced file or symlink must never be hashed."""
    path = Path(path)
    before = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    if expected is not None:
        _check_identity(path, before, expected)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{path}: not a regular file (symlinks are not allowed)")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags, dir_fd=dir_fd)
    try:
        after = os.fstat(fd)
        if (not stat.S_ISREG(after.st_mode)
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            raise ValueError(f"{path}: file changed while being read")
        with os.fdopen(fd, "rb") as handle:
            fd = None
            yield handle
            _check_binding(path, after, dir_fd=dir_fd)
    finally:
        if fd is not None:
            os.close(fd)


@contextmanager
def _regular_file_at(root_fd, relative, *, recorded=None):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{relative}: expected a path inside the package")
    with _directory_parts(root_fd, relative.parts[:-1], recorded=recorded) as directory:
        with _regular_file(relative.name, dir_fd=directory,
                           expected=_recorded_info(recorded, relative.parts)) as handle:
            yield handle


def digest(path, *, root_fd=None, recorded=None):
    result = hashlib.sha256()
    reader = (_regular_file(path) if root_fd is None
              else _regular_file_at(root_fd, path, recorded=recorded))
    with reader as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            result.update(chunk)
    return result.hexdigest()


def manifest_text(root):
    root = Path(root)
    with _directory_fd(root) as root_fd:
        recorded = {}
        paths, problems = _scan(root, root_fd=root_fd, recorded=recorded)
        if problems:
            raise ValueError("; ".join(problems))
        text = "".join(f"{digest(p.relative_to(root), root_fd=root_fd, recorded=recorded)}  "
                       f"{p.relative_to(root).as_posix()}\n" for p in paths)
        problems = _verify_recorded(root, root_fd, recorded)
        if problems:
            raise ValueError("; ".join(problems))
        return text


def check(copy):
    """Problems with copy against its own MANIFEST (empty when intact)."""
    copy = Path(copy)
    try:
        with _directory_fd(copy) as root_fd:
            return _check_opened(copy, root_fd)
    except (OSError, ValueError) as e:
        return [f"{copy}: cannot verify package directory: {e}"]


def _check_opened(copy, root_fd):
    recorded = {}
    paths, problems = _scan(copy, root_fd=root_fd, recorded=recorded)
    _check_binding(copy, os.fstat(root_fd))
    listed = copy / MANIFEST
    try:
        with _regular_file_at(root_fd, MANIFEST, recorded=recorded) as handle:
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
            have[rel] = digest(rel, root_fd=root_fd, recorded=recorded)
        except (OSError, ValueError) as e:
            problems.append(f"{rel}: cannot read package file: {e}")
    problems += [f"{rel}: edited in the vendored copy" for rel in sorted(want) if rel in have and have[rel] != want[rel]]
    problems += [f"{rel}: missing from the vendored copy" for rel in sorted(set(want) - set(have))]
    problems += [f"{rel}: not part of scan-report-kit" for rel in sorted(set(have) - set(want))]
    problems += _verify_recorded(copy, root_fd, recorded)
    return problems


def vendor(dest):
    _require_dir_fds()
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
