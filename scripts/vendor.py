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
import hashlib
import shutil
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "scan_report_kit"
NAME = "scan_report_kit"
MANIFEST = "MANIFEST"


def files(root):
    return sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                  and p.suffix != ".pyc" and p.name != MANIFEST)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_text(root):
    return "".join(f"{digest(p)}  {p.relative_to(root).as_posix()}\n" for p in files(root))


def check(copy):
    """Problems with copy against its own MANIFEST (empty when intact)."""
    listed = copy / MANIFEST
    if not listed.is_file():
        return [f"{listed}: missing"]
    want = {}
    for line in listed.read_text(encoding="utf-8").splitlines():
        sha, _, rel = line.partition("  ")
        want[rel] = sha
    have = {p.relative_to(copy).as_posix(): digest(p) for p in files(copy)}
    problems = [f"{rel}: edited in the vendored copy" for rel in sorted(want) if rel in have and have[rel] != want[rel]]
    problems += [f"{rel}: missing from the vendored copy" for rel in sorted(set(want) - set(have))]
    problems += [f"{rel}: not part of scan-report-kit" for rel in sorted(set(have) - set(want))]
    return problems


def vendor(dest):
    target = Path(dest) / NAME
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise SystemExit(f"vendor.py: refusing to replace {target}: not a directory")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", MANIFEST))
    (target / MANIFEST).write_text(manifest_text(target), encoding="utf-8")
    return target


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("dest", help="directory that receives (or holds) scan_report_kit/")
    p.add_argument("--check", action="store_true", help="verify the copy against its MANIFEST")
    a = p.parse_args(argv)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
