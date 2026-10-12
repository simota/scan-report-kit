"""Regression checks for preserving source/copies and validating vendored files."""

import contextlib
import io
import os
from pathlib import Path
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
            opened.assert_not_called()
        alias = self.root / "linked-copy"
        alias.symlink_to(target, target_is_directory=True)
        with patch.object(vendor.os, "open", wraps=os.open) as opened:
            self.assertTrue(vendor.check(alias))
            opened.assert_not_called()

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
