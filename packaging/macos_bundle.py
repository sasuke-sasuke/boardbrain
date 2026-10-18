"""Clean signing-blocking metadata on a built copy, then sign and verify it."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


BLOCKING_ATTRIBUTES = frozenset(("com.apple.FinderInfo", "com.apple.ResourceFork"))


def list_attributes(path: Path) -> set[str]:
    # Python's os.*xattr API is Linux-only. Use the macOS tool; -s means do not
    # follow symbolic links. Each path is a separate argument, including spaces.
    result = subprocess.run(["/usr/bin/xattr", "-s", str(path)],
                            check=True, capture_output=True, text=True)
    return set(result.stdout.splitlines())


def remove_attribute(path: Path, name: str) -> None:
    subprocess.run(["/usr/bin/xattr", "-s", "-d", name, str(path)], check=True)


def clean_bundle_metadata(bundle: Path) -> int:
    bundle = Path(bundle).absolute()
    if bundle.name != "BoardBrain.app" or bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("Expected a built BoardBrain.app directory")
    executable = bundle / "Contents" / "MacOS" / "BoardBrain"
    if not executable.is_file() or executable.is_symlink():
        raise ValueError("BoardBrain.app is missing its executable")

    def fail(error):
        raise error

    def paths():
        yield bundle
        for folder, directories, files in os.walk(bundle, followlinks=False, onerror=fail):
            for name in directories + files:
                yield Path(folder) / name

    removed = 0
    for path in paths():
        # Do not follow bundle symlinks or remove quarantine/other metadata.
        for name in list_attributes(path) & BLOCKING_ATTRIBUTES:
            remove_attribute(path, name)
            removed += 1
    return removed


def sign_and_verify(bundle: Path) -> None:
    bundle = Path(bundle).absolute()
    removed = clean_bundle_metadata(bundle)
    print(f"Prepared app for signing: removed {removed} Finder/resource-fork attributes.", flush=True)
    # Match PyInstaller's local ad-hoc bundle signing, after metadata cleanup.
    # A signing or strict verification error must prevent installation.
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-",
                    "--timestamp=none", str(bundle)], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict",
                    "--verbose=2", str(bundle)], check=True)


if __name__ == "__main__":
    if sys.platform != "darwin" or len(sys.argv) != 2:
        raise SystemExit("Usage on macOS: python3 packaging/macos_bundle.py /path/to/BoardBrain.app")
    sign_and_verify(Path(sys.argv[1]))
