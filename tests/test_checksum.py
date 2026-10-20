"""Offline behavior checks; no installer or network is required."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "aimens_checksum.py"
spec = importlib.util.spec_from_file_location("aimens_checksum", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class ChecksumTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.content = b"Aimens fixture\x00" * 200000 + b"end"
        self.file = self.root / "fixture.apk"
        self.file.write_bytes(self.content)
        self.release = {
            "id": "android-fixture", "label": "Fixture", "filename": "fixture.apk",
            "size_bytes": len(self.content), "sha256": hashlib.sha256(self.content).hexdigest(),
            "download_url": "https://aimensproxy.com/downloads/files/fixture.apk",
        }
        self.data = {"schema_version": 1, "reference_date": "2026-10-20",
                     "official_site": "https://aimensproxy.com/",
                     "source": "https://aimensproxy.com/downloads/", "releases": [self.release]}

    def command(self, args):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(checker, "load_manifest", return_value=self.data), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = checker.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_streams_across_multiple_blocks(self):
        result = checker.compare_file(self.file, self.release, self.data["reference_date"])
        self.assertGreater(len(self.content), checker.BLOCK_BYTES)
        self.assertEqual(result["sha256"], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(result["status"], "match")

    def test_same_size_changed_byte_fails(self):
        changed = bytearray(self.content)
        changed[1024] ^= 1
        self.file.write_bytes(changed)
        self.assertEqual(checker.compare_file(self.file, self.release, "2026-10-20")["status"], "mismatch")

    def test_truncated_download_fails(self):
        self.file.write_bytes(self.content[:-1])
        result = checker.compare_file(self.file, self.release, "2026-10-20")
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["size_bytes"], len(self.content) - 1)

    def test_empty_download_fails(self):
        self.file.write_bytes(b"")
        self.assertEqual(checker.compare_file(self.file, self.release, "2026-10-20")["status"], "mismatch")

    def test_size_mismatch_fails_even_if_digest_matches(self):
        reference = dict(self.release, size_bytes=len(self.content) + 1)
        self.assertEqual(checker.compare_file(self.file, reference, "2026-10-20")["status"], "mismatch")

    def test_renamed_file_needs_explicit_release(self):
        with self.assertRaises(ValueError):
            checker.select_release(self.data, "renamed.apk")
        self.assertEqual(checker.select_release(self.data, "renamed.apk", "android-fixture"), self.release)

    def test_unknown_release_rejected(self):
        with self.assertRaises(ValueError):
            checker.select_release(self.data, "fixture.apk", "not-a-release")

    def test_cli_json_match_and_exit_zero(self):
        code, out, err = self.command([str(self.file), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "match")
        self.assertEqual(err, "")
        self.assertNotIn(str(self.root), out)

    def test_cli_mismatch_exits_one(self):
        self.file.write_bytes(b"different")
        code, out, err = self.command([str(self.file), "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "mismatch")

    def test_cli_missing_file_exits_two(self):
        code, out, err = self.command([str(self.root / "missing.apk"), "--release", "android-fixture", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["status"], "error")

    def test_directory_is_not_installer(self):
        with self.assertRaises(ValueError):
            checker.compare_file(self.root, self.release, "2026-10-20")

    def test_unicode_and_space_filename(self):
        renamed = self.root / "Айменс копия (1).apk"
        self.file.rename(renamed)
        code, out, err = self.command([str(renamed), "--release", "android-fixture", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["file_name"], renamed.name)

    def test_list_and_file_are_incompatible(self):
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            checker.main([str(self.file), "--list"])
        self.assertEqual(caught.exception.code, 2)

    def test_no_argument_is_usage_error(self):
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            checker.main([])
        self.assertEqual(caught.exception.code, 2)

    def test_list_is_local_json(self):
        code, out, err = self.command(["--list"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), self.data)

    def test_invalid_reference_variants_rejected(self):
        mutations = [
            lambda x: x.update(schema_version=2),
            lambda x: x.update(official_site="https://example.com/"),
            lambda x: x.update(releases=[]),
            lambda x: x["releases"].append(copy.deepcopy(x["releases"][0])),
            lambda x: x["releases"][0].update(sha256="0"),
            lambda x: x["releases"][0].update(size_bytes=True),
            lambda x: x["releases"][0].update(size_bytes=-1),
            lambda x: x["releases"][0].update(filename="../fixture.apk"),
            lambda x: x["releases"][0].update(download_url="https://example.com/fixture.apk"),
        ]
        for change in mutations:
            with self.subTest(change=change):
                value = copy.deepcopy(self.data)
                change(value)
                manifest = self.root / "test-manifest.json"
                manifest.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    checker.load_manifest(manifest)

    def test_manifest_root_must_be_object(self):
        manifest = self.root / "bad-root.json"
        manifest.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            checker.load_manifest(manifest)

    def test_public_reference_snapshot_loads(self):
        data = checker.load_manifest()
        self.assertEqual({x["id"] for x in data["releases"]}, {"android-1.0.53", "windows-7.28.0"})


if __name__ == "__main__":
    unittest.main()
