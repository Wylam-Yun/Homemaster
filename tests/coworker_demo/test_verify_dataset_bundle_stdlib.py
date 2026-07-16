from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.coworker_demo.verify_dataset_bundle import (
    DatasetVerificationError,
    verify_bundle,
)


class VerifyDatasetBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.root = Path(self._tempdir.name) / "case_02"
        self.root.mkdir()
        self._write_fixture()

    def test_valid_bundle_accepts_utf8_bom_and_matches_counts(self) -> None:
        report = verify_bundle(self.root)

        self.assertEqual(report.declared_file_count, 8)
        self.assertEqual(report.verified_file_count, 8)
        self.assertEqual(report.record_counts["tool_catalog"], 1)
        self.assertEqual(report.record_counts["black_screen_output"], 1)
        self.assertEqual(report.record_counts["mcp_query_config.change_time_window"], 1)

    def test_missing_declared_file_is_rejected(self) -> None:
        (self.root / "test_set" / "tool_catalog.json").unlink()

        with self.assertRaisesRegex(DatasetVerificationError, "missing declared file"):
            verify_bundle(self.root)

    def test_one_byte_hash_drift_is_rejected(self) -> None:
        path = self.root / "test_set" / "operation_access_log.json"
        path.write_bytes(path.read_bytes() + b" ")

        with self.assertRaisesRegex(DatasetVerificationError, "sha256 mismatch"):
            verify_bundle(self.root)

    def test_record_count_drift_is_rejected(self) -> None:
        manifest_path = self.root / "dataset_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["record_counts"]["tool_catalog"] = 2
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(DatasetVerificationError, "record count mismatch"):
            verify_bundle(self.root)

    def test_manifest_path_escape_is_rejected(self) -> None:
        outside = self.root.parent / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        manifest_path = self.root / "dataset_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["contract"]["file_sha256"]["../outside.json"] = _sha256(outside)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(DatasetVerificationError, "escapes bundle root"):
            verify_bundle(self.root)

    def _write_fixture(self) -> None:
        files: dict[str, bytes] = {
            "README.md": b"fixture\n",
            "test_set/item_change_ticket.json": b'{"sop_type":"CHANGE_SOP"}',
            "test_set/tool_catalog.json": b'\xef\xbb\xbf[{"toolId":"tool-1"}]',
            "test_set/monitor_cls_query_request.json": b"[{}]",
            "test_set/operation_access_log.json": b"[{}]",
            "test_set/black_screen_output.json": (
                b'{"cmd_infos":[{"cmd":"grep"}],"total":1,"type":"fixture"}'
            ),
            "test_set/mcp_query_config.json": (
                b'{"change_time_window":{"begin_time":1,"end_time":2}}'
            ),
            "ground_truth/validation_chain_ground_truth.json": b'[{"case_id":"c1"}]',
        }
        for relative_path, content in files.items():
            path = self.root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        manifest = {
            "dataset_id": "fixture",
            "input_ticket": "test_set/item_change_ticket.json",
            "data_files": {
                "tool_catalog": "test_set/tool_catalog.json",
                "mcp_query_config": "test_set/mcp_query_config.json",
                "mcp_request_samples": "test_set/monitor_cls_query_request.json",
                "mcp_response_logs": "test_set/operation_access_log.json",
                "black_screen_output": "test_set/black_screen_output.json",
                "ground_truth": "ground_truth/validation_chain_ground_truth.json",
            },
            "record_counts": {
                "tool_catalog": 1,
                "monitor_cls_query_request": 1,
                "operation_access_log": 1,
                "validation_chain_ground_truth": 1,
                "mcp_query_config.change_time_window": 1,
                "black_screen_output": 1,
            },
            "contract": {
                "schema_version": 1,
                "file_sha256": {
                    relative_path: _sha256(self.root / relative_path)
                    for relative_path in sorted(files)
                },
            },
        }
        (self.root / "dataset_manifest.json").write_text(
            json.dumps(manifest, sort_keys=True),
            encoding="utf-8",
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
