"""Regression checks for preserving source/copies and validating vendored files."""

import contextlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import vendor  # noqa: E402


class VendorRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "upstream" / vendor.NAME
        self.source.mkdir(parents=True)
        (self.source / "__init__.py").write_text("value = 'upstream'\n", encoding="utf-8")
        (self.source / "assets").mkdir()
        (self.source / "assets" / "page.css").write_text("body{}\n", encoding="utf-8")
        self.dest = self.root / "consumer" / "scripts"
        self.package_patch = patch.object(vendor, "PACKAGE", self.source)
        self.package_patch.start()

    def tearDown(self):
        self.package_patch.stop()
        self.tmp.cleanup()

    def old_copy(self):
        target = self.dest / vendor.NAME
        target.mkdir(parents=True)
        (target / "old.py").write_text("keep the working copy\n", encoding="utf-8")
        return target

    def assert_old_copy(self, target):
        self.assertEqual((target / "old.py").read_text(encoding="utf-8"), "keep the working copy\n")
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), [vendor.NAME])

    def same_content_replacement(self, entry):
        work = Path(tempfile.mkdtemp(prefix="replacement-", dir=self.root))
        replacement, previous = work / "replacement", work / "previous"
        if entry.is_dir():
            shutil.copytree(entry, replacement)
        else:
            shutil.copy2(entry, replacement)
        info = replacement.stat()

        def replace():
            entry.rename(previous)
            os.replace(replacement, entry)

        return replace, (info.st_dev, info.st_ino)

    @contextlib.contextmanager
    def opened_inodes(self):
        opened = set()
        original_open = os.open

        def track_open(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            info = os.fstat(fd)
            opened.add((info.st_dev, info.st_ino))
            return fd

        with patch.object(vendor.os, "open", side_effect=track_open):
            yield opened

    def test_refuses_to_replace_source_itself(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            vendor.vendor(self.source.parent)
        self.assertEqual((self.source / "__init__.py").read_text(encoding="utf-8"), "value = 'upstream'\n")

    def test_refuses_to_copy_inside_source(self):
        destination = self.source / "child"
        with self.assertRaisesRegex(ValueError, "overlap"):
            vendor.vendor(destination)
        self.assertFalse(destination.exists())

    def test_refuses_to_replace_ancestor_of_source(self):
        containing = self.root / vendor.NAME
        nested = containing / "upstream" / vendor.NAME
        nested.mkdir(parents=True)
        (nested / "keep.py").write_text("keep\n", encoding="utf-8")
        with patch.object(vendor, "PACKAGE", nested):
            with self.assertRaisesRegex(ValueError, "overlap"):
                vendor.vendor(self.root)
        self.assertEqual((nested / "keep.py").read_text(encoding="utf-8"), "keep\n")

    def test_source_alias_is_also_refused(self):
        alias = self.root / "source-alias"
        alias.symlink_to(self.source.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "overlap"):
            vendor.vendor(alias)
        self.assertTrue((self.source / "__init__.py").is_file())

    def test_stages_valid_copy_and_preserves_nested_manifest_file(self):
        (self.source / "assets" / vendor.MANIFEST).write_text("asset metadata\n", encoding="utf-8")
        cache = self.source / "__pycache__"
        cache.mkdir()
        (cache / "__init__.pyc").write_bytes(b"bytecode")
        target = vendor.vendor(self.dest)
        self.assertEqual(vendor.check(target), [])
        self.assertEqual((target / "assets" / vendor.MANIFEST).read_text(encoding="utf-8"), "asset metadata\n")
        self.assertFalse((target / "__pycache__").exists())
        (target / "__pycache__").mkdir()
        (target / "__pycache__" / "runtime.pyc").write_bytes(b"runtime cache")
        self.assertEqual(vendor.check(target), [])

    def test_failed_copy_keeps_existing_package(self):
        target = self.old_copy()
        real_copytree = vendor.shutil.copytree

        def failed_copy(*args, **kwargs):
            real_copytree(*args, **kwargs)
            raise OSError("injected copy failure")

        with patch.object(vendor.shutil, "copytree", side_effect=failed_copy):
            with self.assertRaisesRegex(OSError, "injected copy failure"):
                vendor.vendor(self.dest)
        self.assert_old_copy(target)

    def test_failed_install_restores_existing_package(self):
        target = self.old_copy()
        real_replace = vendor.os.replace

        def failed_install(source, destination):
            if Path(source).name == "package":
                raise OSError("injected install failure")
            return real_replace(source, destination)

        with patch.object(vendor.os, "replace", side_effect=failed_install):
            with self.assertRaisesRegex(OSError, "injected install failure"):
                vendor.vendor(self.dest)
        self.assert_old_copy(target)

    def test_failed_restore_retains_original_at_reported_path(self):
        self.old_copy()
        real_replace = vendor.os.replace

        def failed_install_and_restore(source, destination):
            if Path(source).name in ("package", "previous"):
                raise OSError("injected rename failure")
            return real_replace(source, destination)

        with patch.object(vendor.os, "replace", side_effect=failed_install_and_restore):
            with self.assertRaisesRegex(OSError, "original copy is preserved at") as caught:
                vendor.vendor(self.dest)
        backups = list(self.dest.glob(f".{vendor.NAME}.vendor-*/previous/old.py"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "keep the working copy\n")
        self.assertIn(str(backups[0].parent), str(caught.exception))

    def test_interruption_after_moving_original_restores_it(self):
        target = self.old_copy()
        real_replace = vendor.os.replace

        def interrupted_move(source, destination):
            result = real_replace(source, destination)
            if Path(destination).name == "previous":
                raise KeyboardInterrupt()
            return result

        with patch.object(vendor.os, "replace", side_effect=interrupted_move):
            with self.assertRaises(KeyboardInterrupt):
                vendor.vendor(self.dest)
        self.assert_old_copy(target)

    def test_interruption_after_install_restores_existing_package(self):
        target = self.old_copy()
        real_replace = vendor.os.replace

        def interrupted_install(source, destination):
            result = real_replace(source, destination)
            if Path(source).name == "package":
                raise KeyboardInterrupt("interrupted after install")
            return result

        with patch.object(vendor.os, "replace", side_effect=interrupted_install):
            with self.assertRaisesRegex(KeyboardInterrupt, "interrupted after install"):
                vendor.vendor(self.dest)
        self.assert_old_copy(target)

    def test_interruption_after_install_removes_new_package(self):
        real_replace = vendor.os.replace

        def interrupted_install(source, destination):
            result = real_replace(source, destination)
            if Path(source).name == "package":
                raise KeyboardInterrupt("interrupted after install")
            return result

        with patch.object(vendor.os, "replace", side_effect=interrupted_install):
            with self.assertRaisesRegex(KeyboardInterrupt, "interrupted after install"):
                vendor.vendor(self.dest)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_failed_rollback_after_install_keeps_recovery_copies(self):
        real_replace = vendor.os.replace
        cases = ((True, "withdraw"), (True, "restore"), (False, "withdraw"))
        for existing, failed_move in cases:
            with self.subTest(existing=existing, failed_move=failed_move):
                dest = self.dest / f"{existing}-{failed_move}"
                target = dest / vendor.NAME
                if existing:
                    target.mkdir(parents=True)
                    (target / "old.py").write_text("keep the working copy\n", encoding="utf-8")

                def failed_rollback(source, destination):
                    if ((failed_move == "withdraw" and Path(destination).name == "uncommitted")
                            or (failed_move == "restore" and Path(source).name == "previous")):
                        raise PermissionError("injected rollback permission failure")
                    result = real_replace(source, destination)
                    if Path(source).name == "package":
                        raise KeyboardInterrupt("interrupted after install")
                    return result

                with patch.object(vendor.os, "replace", side_effect=failed_rollback):
                    with self.assertRaisesRegex(OSError, "could not restore previous state") as caught:
                        vendor.vendor(dest)
                recovery = list(dest.glob(f".{vendor.NAME}.vendor-*"))
                self.assertEqual(len(recovery), 1)
                if existing:
                    backup = recovery[0] / "previous"
                    self.assertEqual((backup / "old.py").read_text(encoding="utf-8"), "keep the working copy\n")
                    self.assertIn(f"original copy is preserved at {backup}", str(caught.exception))
                else:
                    self.assertIn(f"destination is preserved at {target}", str(caught.exception))
                    self.assertFalse((recovery[0] / "previous").exists())
                installed = target if failed_move == "withdraw" else recovery[0] / "uncommitted"
                self.assertEqual(vendor.check(installed), [])
                self.assertEqual(target.exists(), failed_move == "withdraw")

    def test_interruption_after_rollback_rename_still_finishes_restoration(self):
        target = self.old_copy()
        real_replace = vendor.os.replace
        for interrupted_move in ("withdraw", "restore"):
            with self.subTest(interrupted_move=interrupted_move):
                def interrupted_rollback(source, destination):
                    result = real_replace(source, destination)
                    if Path(source).name == "package":
                        raise OSError("injected install failure after rename")
                    if ((interrupted_move == "withdraw" and Path(destination).name == "uncommitted")
                            or (interrupted_move == "restore" and Path(source).name == "previous")):
                        raise KeyboardInterrupt("interrupted after rollback rename")
                    return result

                with patch.object(vendor.os, "replace", side_effect=interrupted_rollback):
                    with self.assertRaisesRegex(OSError, "injected install failure after rename"):
                        vendor.vendor(self.dest)
                self.assert_old_copy(target)

    def test_failed_install_preserves_unexpected_destination(self):
        real_replace = vendor.os.replace
        for existing in (True, False):
            with self.subTest(existing=existing):
                dest = self.dest / str(existing)
                target = dest / vendor.NAME
                if existing:
                    target.mkdir(parents=True)
                    (target / "old.py").write_text("keep the working copy\n", encoding="utf-8")

                def failed_install(source, destination):
                    if Path(source).name == "package":
                        target.mkdir()
                        (target / "other.py").write_text("another process's file\n", encoding="utf-8")
                        raise OSError("injected install failure")
                    return real_replace(source, destination)

                with patch.object(vendor.os, "replace", side_effect=failed_install):
                    with self.assertRaisesRegex(OSError, "refusing to replace unexpected destination") as caught:
                        vendor.vendor(dest)
                self.assertEqual((target / "other.py").read_text(encoding="utf-8"), "another process's file\n")
                self.assertEqual(sorted(path.name for path in target.iterdir()), ["other.py"])
                recovery = list(dest.glob(f".{vendor.NAME}.vendor-*"))
                self.assertEqual(len(recovery), 1)
                self.assertEqual(vendor.check(recovery[0] / "package"), [])
                if existing:
                    backup = recovery[0] / "previous"
                    self.assertEqual((backup / "old.py").read_text(encoding="utf-8"), "keep the working copy\n")
                    self.assertIn(str(backup), str(caught.exception))

    def test_source_symlink_is_refused_before_existing_copy_is_changed(self):
        target = self.old_copy()
        outside = self.root / "outside.py"
        outside.write_text("external\n", encoding="utf-8")
        (self.source / "external.py").symlink_to(outside)
        with patch.object(vendor.shutil, "copytree") as copytree:
            with self.assertRaisesRegex(ValueError, "symlinks"):
                vendor.vendor(self.dest)
            copytree.assert_not_called()
        self.assert_old_copy(target)

    def test_destination_symlink_is_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep").write_text("external\n", encoding="utf-8")
        self.dest.mkdir(parents=True)
        target = self.dest / vendor.NAME
        target.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "not a directory"):
            vendor.vendor(self.dest)
        self.assertTrue(target.is_symlink())
        self.assertEqual((outside / "keep").read_text(encoding="utf-8"), "external\n")

    def test_check_rejects_duplicate_hash_and_unsafe_path_entries(self):
        target = vendor.vendor(self.dest)
        listed = target / vendor.MANIFEST
        good = listed.read_text(encoding="utf-8")
        entry = good.splitlines()[0]
        cases = {
            "duplicate": good + entry + "\n",
            "hash": good + "not-a-hash  extra.py\n",
            "absolute": good + "a" * 64 + "  /external.py\n",
            "parent": good + "a" * 64 + "  ../external.py\n",
            "windows": good + "a" * 64 + "  C:\\external.py\n",
            "empty": "",
            "empty-line": good + "\n",
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                listed.write_text(content, encoding="utf-8")
                self.assertTrue(any("MANIFEST" in p for p in vendor.check(target)))

    def test_check_never_opens_linked_package_files(self):
        target = vendor.vendor(self.dest)
        regular = target / "__init__.py"
        outside = self.root / "outside.py"
        outside.write_bytes(regular.read_bytes())
        regular.unlink()
        regular.symlink_to(outside)
        with patch.object(vendor.os, "open", wraps=os.open) as opened:
            problems = vendor.check(target)
        self.assertTrue(any("__init__.py: symlinks" in p for p in problems))
        self.assertNotIn(regular, [Path(call.args[0]) for call in opened.call_args_list])
        self.assertNotIn(outside, [Path(call.args[0]) for call in opened.call_args_list])

    def test_check_never_opens_linked_manifest_or_linked_root(self):
        target = vendor.vendor(self.dest)
        manifest = target / vendor.MANIFEST
        outside = self.root / "external-manifest"
        outside.write_bytes(manifest.read_bytes())
        manifest.unlink()
        manifest.symlink_to(outside)
        with patch.object(vendor.os, "open", wraps=os.open) as opened:
            self.assertTrue(vendor.check(target))
            self.assertNotIn(vendor.MANIFEST, [Path(call.args[0]).name for call in opened.call_args_list])
        alias = self.root / "linked-copy"
        alias.symlink_to(target, target_is_directory=True)
        with patch.object(vendor.os, "open", wraps=os.open) as opened:
            self.assertTrue(vendor.check(alias))
            opened.assert_not_called()

    def test_check_reports_root_replaced_after_scanning(self):
        original_scan = vendor._scan
        for replacement in ("missing", "symlink", "regular-file"):
            with self.subTest(replacement=replacement):
                target = vendor.vendor(self.dest / replacement)
                moved = target.with_name("previous-package")

                def replace_after_scan(copy, **kwargs):
                    result = original_scan(copy, **kwargs)
                    target.rename(moved)
                    if replacement == "symlink":
                        target.symlink_to(moved, target_is_directory=True)
                    elif replacement == "regular-file":
                        target.write_text("not a package\n", encoding="utf-8")
                    return result

                with patch.object(vendor, "_scan", side_effect=replace_after_scan):
                    with patch.object(vendor, "_regular_file", wraps=vendor._regular_file) as opened:
                        problems = vendor.check(target)
                self.assertTrue(problems, "a changed package root must not pass its integrity check")
                self.assertTrue(any(str(target) in problem for problem in problems))
                opened.assert_not_called()

    def test_root_swap_before_manifest_read_never_opens_the_replacement_tree(self):
        target = vendor.vendor(self.dest)
        outside = self.root / "outside-package"
        shutil.copytree(target, outside)
        moved = target.with_name("original-package")
        outside_files = {(path.stat().st_dev, path.stat().st_ino)
                         for path in outside.rglob("*") if path.is_file()}
        opened_files = set()
        original_open, original_file = os.open, vendor._regular_file
        swapped = False

        def track_open(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            info = os.fstat(fd)
            opened_files.add((info.st_dev, info.st_ino))
            return fd

        @contextlib.contextmanager
        def swap_before_manifest(path, *args, **kwargs):
            nonlocal swapped
            if Path(path).name == vendor.MANIFEST and not swapped:
                target.rename(moved)
                target.symlink_to(outside, target_is_directory=True)
                swapped = True
            with original_file(path, *args, **kwargs) as handle:
                yield handle

        with patch.object(vendor, "_regular_file", side_effect=swap_before_manifest):
            with patch.object(vendor.os, "open", side_effect=track_open):
                problems = vendor.check(target)
        self.assertTrue(swapped)
        self.assertTrue(problems, "a root swapped for a symlink must not pass verification")
        self.assertFalse(opened_files & outside_files, "verification must not read through the substituted root")

    def test_nested_swap_before_hashing_never_opens_the_replacement_tree(self):
        target = vendor.vendor(self.dest)
        directory = target / "assets"
        outside = self.root / "outside-assets"
        shutil.copytree(directory, outside)
        moved = target / "original-assets"
        outside_file = (outside / "page.css").stat()
        opened_files = set()
        original_open, original_file = os.open, vendor._regular_file
        swapped = False

        def track_open(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            info = os.fstat(fd)
            opened_files.add((info.st_dev, info.st_ino))
            return fd

        @contextlib.contextmanager
        def swap_before_hash(path, *args, **kwargs):
            nonlocal swapped
            if Path(path).name == "page.css" and not swapped:
                directory.rename(moved)
                directory.symlink_to(outside, target_is_directory=True)
                swapped = True
            with original_file(path, *args, **kwargs) as handle:
                yield handle

        with patch.object(vendor, "_regular_file", side_effect=swap_before_hash):
            with patch.object(vendor.os, "open", side_effect=track_open):
                problems = vendor.check(target)
        self.assertTrue(swapped)
        self.assertTrue(problems, "a nested directory swapped for a symlink must not pass verification")
        self.assertNotIn((outside_file.st_dev, outside_file.st_ino), opened_files)

    def test_nested_swap_during_scan_never_scans_the_replacement_tree(self):
        target = vendor.vendor(self.dest)
        directory = target / "assets"
        original = directory.stat()
        outside = self.root / "outside-assets"
        shutil.copytree(directory, outside)
        external = outside.stat()
        moved = target / "original-assets"
        original_scandir = os.scandir
        scanned = set()
        swapped = False

        def swap_before_scan(path):
            nonlocal swapped
            before = os.fstat(path) if isinstance(path, int) else Path(path).stat()
            if (before.st_dev, before.st_ino) == (original.st_dev, original.st_ino) and not swapped:
                directory.rename(moved)
                directory.symlink_to(outside, target_is_directory=True)
                swapped = True
            after = os.fstat(path) if isinstance(path, int) else Path(path).stat()
            scanned.add((after.st_dev, after.st_ino))
            return original_scandir(path)

        with patch.object(vendor.os, "scandir", side_effect=swap_before_scan):
            problems = vendor.check(target)
        self.assertTrue(swapped)
        self.assertTrue(problems, "a directory substituted while scanning must not pass verification")
        self.assertNotIn((external.st_dev, external.st_ino), scanned)

    def test_discovered_directory_replacement_is_rejected_before_opening_it(self):
        (self.source / "assets" / "nested").mkdir()
        (self.source / "assets" / "nested" / "leaf.css").write_text("a{}\n", encoding="utf-8")
        original_directory = vendor._directory_fd
        for relative in ("assets", "assets/nested"):
            with self.subTest(relative=relative):
                target = vendor.vendor(self.dest / relative)
                victim = target / relative
                replace, external_inode = self.same_content_replacement(victim)
                swapped = False

                @contextlib.contextmanager
                def swap_before_open(path, *args, **kwargs):
                    nonlocal swapped
                    if Path(path).name == victim.name and not swapped:
                        replace()
                        swapped = True
                    with original_directory(path, *args, **kwargs) as fd:
                        yield fd

                with patch.object(vendor, "_directory_fd", side_effect=swap_before_open):
                    with self.opened_inodes() as opened:
                        problems = vendor.check(target)
                self.assertTrue(swapped)
                self.assertTrue(problems, "enumerated directory identity must survive pending traversal")
                self.assertNotIn(external_inode, opened, "a replacement must be rejected before it is opened")

    def test_recorded_entry_replacement_after_scan_is_rejected_before_opening_it(self):
        (self.source / "empty").mkdir()
        original_scan = vendor._scan
        for relative in ("assets", "empty", "__init__.py", vendor.MANIFEST):
            with self.subTest(relative=relative):
                target = vendor.vendor(self.dest / relative)
                replace, external_inode = self.same_content_replacement(target / relative)

                def swap_after_scan(*args, **kwargs):
                    result = original_scan(*args, **kwargs)
                    replace()
                    return result

                with patch.object(vendor, "_scan", side_effect=swap_after_scan):
                    with self.opened_inodes() as opened:
                        problems = vendor.check(target)
                self.assertTrue(problems, "same contents must not hide a recorded entry's replacement")
                self.assertNotIn(external_inode, opened, "a replacement must be rejected before it is opened")

    def test_recorded_entries_are_rechecked_after_the_last_hash(self):
        (self.source / "empty").mkdir()
        original_digest = vendor.digest
        for relative in ("assets", "empty", "__init__.py", vendor.MANIFEST):
            with self.subTest(relative=relative):
                target = vendor.vendor(self.dest / relative)
                replace, external_inode = self.same_content_replacement(target / relative)
                swapped = False

                def swap_after_last_hash(path, *args, **kwargs):
                    nonlocal swapped
                    result = original_digest(path, *args, **kwargs)
                    if Path(path).as_posix() == "assets/page.css":
                        replace()
                        swapped = True
                    return result

                with patch.object(vendor, "digest", side_effect=swap_after_last_hash):
                    with self.opened_inodes() as opened:
                        problems = vendor.check(target)
                self.assertTrue(swapped)
                self.assertTrue(problems, "all recorded identities, including empty directories, need a final check")
                self.assertNotIn(external_inode, opened)

    def test_manifest_identity_is_rechecked_after_reading_it(self):
        target = vendor.vendor(self.dest)
        replace, _ = self.same_content_replacement(target / vendor.MANIFEST)
        original_file = vendor._regular_file_at

        @contextlib.contextmanager
        def swap_after_manifest_read(root_fd, relative, **kwargs):
            with original_file(root_fd, relative, **kwargs) as handle:
                yield handle
            if Path(relative).as_posix() == vendor.MANIFEST:
                replace()

        with patch.object(vendor, "_regular_file_at", side_effect=swap_after_manifest_read):
            self.assertTrue(vendor.check(target), "the MANIFEST identity must survive its reader's closure")

    def test_manifest_generation_preserves_recorded_identities(self):
        (self.source / "empty").mkdir()
        original_scan, original_digest = vendor._scan, vendor.digest
        for timing, relative in (("scan", "__init__.py"), ("hash", "__init__.py"), ("hash", "empty")):
            with self.subTest(timing=timing, relative=relative):
                root = self.root / f"manifest-{timing}-{relative}"
                shutil.copytree(self.source, root)
                replace, external_inode = self.same_content_replacement(root / relative)

                def swap_after_scan(*args, **kwargs):
                    result = original_scan(*args, **kwargs)
                    replace()
                    return result

                def swap_after_last_hash(path, *args, **kwargs):
                    result = original_digest(path, *args, **kwargs)
                    if Path(path).as_posix() == "assets/page.css":
                        replace()
                    return result

                hook = "_scan" if timing == "scan" else "digest"
                action = swap_after_scan if timing == "scan" else swap_after_last_hash
                with patch.object(vendor, hook, side_effect=action):
                    with self.opened_inodes() as opened:
                        with self.assertRaisesRegex(ValueError, "changed during verification"):
                            vendor.manifest_text(root)
                self.assertNotIn(external_inode, opened)

    def test_ignored_cache_updates_do_not_invalidate_recorded_identities(self):
        target = vendor.vendor(self.dest)
        cache = target / "assets" / "__pycache__"
        cache.mkdir()
        (cache / "old.pyc").write_bytes(b"old cache")
        bytecode = target / "module.pyc"
        bytecode.write_bytes(b"old bytecode")
        replace_cache, _ = self.same_content_replacement(cache)
        replace_bytecode, _ = self.same_content_replacement(bytecode)
        original_scan = vendor._scan

        def update_after_scan(*args, **kwargs):
            result = original_scan(*args, **kwargs)
            replace_cache()
            (cache / "new.pyc").write_bytes(b"new cache")
            replace_bytecode()
            (target / "__pycache__").mkdir()
            return result

        with patch.object(vendor, "_scan", side_effect=update_after_scan):
            self.assertEqual(vendor.check(target), [])

    def test_unsupported_directory_descriptors_fail_before_replacing_an_existing_copy(self):
        target = self.old_copy()
        with patch.object(vendor, "_SAFE_DIR_FDS", False, create=True):
            with self.assertRaisesRegex(ValueError, "directory-relative verification"):
                vendor.vendor(self.dest)
            self.assertTrue(any("directory-relative verification" in problem for problem in vendor.check(target)))
        self.assert_old_copy(target)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_check_rejects_fifo_without_opening_it(self):
        target = vendor.vendor(self.dest)
        fifo = target / "unexpected.pipe"
        os.mkfifo(fifo)
        with patch.object(vendor.os, "open", wraps=os.open) as opened:
            problems = vendor.check(target)
        self.assertTrue(any("unexpected.pipe: not a regular" in p for p in problems))
        self.assertNotIn(fifo, [Path(call.args[0]) for call in opened.call_args_list])

    def test_check_reports_invalid_utf8_manifest_without_traceback(self):
        target = vendor.vendor(self.dest)
        (target / vendor.MANIFEST).write_bytes(b"\xff\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status = vendor.main([str(self.dest), "--check"])
        self.assertEqual(status, 1)
        self.assertIn("MANIFEST", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_cli_reports_io_failure_without_traceback(self):
        self.dest.parent.mkdir(parents=True)
        self.dest.write_text("not a directory\n", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status = vendor.main([str(self.dest)])
        self.assertEqual(status, 2)
        self.assertIn("vendor.py:", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
