from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from med_lit_mcp import __version__
from med_lit_mcp.installation import validate_wheel


class PackageValidationTests(unittest.TestCase):
    def test_installer_wheel_resolves_a_portable_package_with_unicode_path(self):
        with tempfile.TemporaryDirectory(prefix="med lit 한글 ") as directory:
            wheel = Path(directory) / f"med_lit_mcp-{__version__}-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("med_lit_mcp.dist-info/METADATA", f"Name: med-lit-mcp\nVersion: {__version__}\n")
            self.assertEqual(validate_wheel(wheel), wheel.resolve())

    def test_wrong_or_damaged_package_is_rejected_with_a_safe_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, metadata in (
                ("package-py3-none-any.whl", f"Name: other\nVersion: {__version__}\n"),
                ("package-py3-none-any.whl", "Name: med-lit-mcp\nVersion: 0.0.0\n"),
                ("package-py3-none-any.whl", f"Version: {__version__}\n"),
                ("package-cp311-cp311-macosx_11_0_arm64.whl", f"Name: med-lit-mcp\nVersion: {__version__}\n"),
            ):
                with self.subTest(name=name, metadata=metadata):
                    wheel = root / name
                    with zipfile.ZipFile(wheel, "w") as archive:
                        archive.writestr("package.dist-info/METADATA", metadata)
                    with self.assertRaisesRegex(ValueError, "valid portable med-lit"):
                        validate_wheel(wheel)
            wheel = root / "damaged-py3-none-any.whl"
            wheel.write_bytes(b"damaged")
            with self.assertRaises(ValueError):
                validate_wheel(wheel)
            wheel.unlink()
            with self.assertRaises(ValueError):
                validate_wheel(wheel)
