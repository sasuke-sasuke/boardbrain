"""Regression checks for the Finder-metadata signing failure seen on a Mac."""
import importlib.util
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "boardbrain_macos_bundle", Path(__file__).resolve().parents[1] / "packaging" / "macos_bundle.py")
bundle_tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle_tools)


class MacPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="boardbrain signing test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "BoardBrain.app"
        self.executable = self.app / "Contents" / "MacOS" / "BoardBrain"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"synthetic executable")

    def test_cleanup_preserves_other_attributes_and_does_not_follow_links(self):
        outside = self.root / "originals"
        outside.mkdir()
        original = outside / "source.txt"
        original.write_bytes(b"unchanged original")
        link = self.app / "Contents" / "external"
        link.symlink_to(outside, target_is_directory=True)
        attrs = {self.app: {"com.apple.FinderInfo", "com.apple.quarantine"},
                 self.executable: {"com.apple.ResourceFork", "com.boardbrain.test"},
                 original: {"com.apple.FinderInfo"},
                 link: {"com.apple.FinderInfo"}}
        visited = []

        def listing(path):
            visited.append(Path(path))
            return attrs.get(Path(path), set()).copy()

        def removal(path, name):
            attrs[Path(path)].remove(name)

        with patch.object(bundle_tools, "list_attributes", side_effect=listing), \
             patch.object(bundle_tools, "remove_attribute", side_effect=removal):
            self.assertEqual(bundle_tools.clean_bundle_metadata(self.app), 3)
            self.assertEqual(bundle_tools.clean_bundle_metadata(self.app), 0)
        self.assertNotIn(original, visited)
        self.assertNotIn(outside, visited)
        self.assertEqual(attrs[self.app], {"com.apple.quarantine"})
        self.assertEqual(attrs[self.executable], {"com.boardbrain.test"})
        self.assertEqual(attrs[original], {"com.apple.FinderInfo"})
        self.assertEqual(original.read_bytes(), b"unchanged original")

    def test_cleanup_rejects_nonbundle_and_symlink_targets(self):
        with self.assertRaises(ValueError):
            bundle_tools.clean_bundle_metadata(self.root)
        alias = self.root / "alias"
        alias.mkdir()
        linked = alias / "BoardBrain.app"
        linked.symlink_to(self.app, target_is_directory=True)
        with self.assertRaises(ValueError):
            bundle_tools.clean_bundle_metadata(linked)

    def test_metadata_error_prevents_signing(self):
        with patch.object(bundle_tools, "list_attributes", side_effect=PermissionError("denied")), \
             patch.object(bundle_tools.subprocess, "run") as run:
            with self.assertRaises(PermissionError):
                bundle_tools.sign_and_verify(self.app)
            run.assert_not_called()

    def test_signing_or_verification_failure_stops_the_build(self):
        for fail_at in (0, 1):
            with self.subTest(fail_at=fail_at):
                calls = []

                def run(command, **kwargs):
                    calls.append(command)
                    self.assertTrue(kwargs["check"])
                    if len(calls) - 1 == fail_at:
                        raise subprocess.CalledProcessError(1, command)

                with patch.object(bundle_tools, "list_attributes", return_value=set()), \
                     patch.object(bundle_tools.subprocess, "run", side_effect=run):
                    with self.assertRaises(subprocess.CalledProcessError):
                        bundle_tools.sign_and_verify(self.app)
                self.assertEqual(len(calls), fail_at + 1)

    @unittest.skipUnless(sys.platform == "darwin", "Requires native macOS codesign and xattrs")
    def test_native_signing_recovers_from_finder_metadata(self):
        shutil.copyfile("/usr/bin/true", self.executable)
        self.executable.chmod(0o755)
        (self.app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleExecutable": "BoardBrain", "CFBundleIdentifier": "com.boardbrain.packaging-test",
            "CFBundleName": "BoardBrain", "CFBundlePackageType": "APPL", "CFBundleVersion": "1"}))
        resource = self.app / "Contents" / "Resources" / "fixture.txt"
        resource.parent.mkdir()
        resource.write_text("synthetic fixture")
        def set_attribute(path, name, value):
            subprocess.run(["/usr/bin/xattr", "-w", "-x", name, value.hex(), str(path)], check=True)

        def get_attribute(path, name):
            result = subprocess.run(["/usr/bin/xattr", "-p", "-x", name, str(path)],
                                    check=True, capture_output=True, text=True)
            return bytes.fromhex(result.stdout)

        set_attribute(self.app, "com.apple.FinderInfo", b"\0" * 8 + b"\x04\0" + b"\0" * 22)
        set_attribute(resource, "com.apple.ResourceFork", b"synthetic resource fork")
        set_attribute(resource, "com.boardbrain.test", b"keep")
        set_attribute(self.app, "com.apple.quarantine", b"0081;00000000;BoardBrain-test;")
        before = subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-",
                                 "--timestamp=none", str(self.app)], capture_output=True, text=True)
        self.assertNotEqual(before.returncode, 0, "Fixture must reproduce the signing failure")
        self.assertIn("resource fork", before.stderr.lower())
        bundle_tools.sign_and_verify(self.app)
        self.assertNotIn("com.apple.FinderInfo", bundle_tools.list_attributes(self.app))
        self.assertNotIn("com.apple.ResourceFork", bundle_tools.list_attributes(resource))
        self.assertEqual(get_attribute(resource, "com.boardbrain.test"), b"keep")
        self.assertEqual(get_attribute(self.app, "com.apple.quarantine"), b"0081;00000000;BoardBrain-test;")
        self.assertEqual(resource.read_text(), "synthetic fixture")


if __name__ == "__main__":
    unittest.main()
