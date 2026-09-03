#!/usr/bin/env python3
"""Directed one-shot ledger gate; no nxrelease, ZIP, device or network."""

import importlib.util
from pathlib import Path
import stat
import tempfile


PORT = Path(__file__).resolve().parents[2]
PIPELINE_PATH = PORT / "recipes/build_public_byo.py"
SPEC = importlib.util.spec_from_file_location("tearscape_pipeline", PIPELINE_PATH)
PIPELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIPELINE)

receipt = {
    "framework_commit": "1" * 40,
    "generation": "2" * 64,
    "port_commit": "3" * 40,
    "port_id": "tearscape",
    "scaffold": {"sha256": "4" * 64},
}

with tempfile.TemporaryDirectory(prefix="tearscape-ledger-") as temporary:
    root = Path(temporary) / "ledger"
    root.mkdir(mode=0o700)
    ledger_id, ledger_sha256 = PIPELINE.claim_external_bundle_attempt_at(
        root, receipt, "5" * 64, "6" * 64,
    )
    ledger = root / f"{ledger_id}.json"
    metadata = ledger.lstat()
    assert stat.S_IMODE(metadata.st_mode) == 0o444
    assert PIPELINE.sha256_file(ledger) == ledger_sha256
    try:
        PIPELINE.claim_external_bundle_attempt_at(
            root, receipt, "5" * 64, "6" * 64,
        )
    except PIPELINE.PipelineError as error:
        assert "already attempted" in str(error)
    else:
        raise AssertionError("a second root could repeat the same release tuple")

print("TEARSCAPE RELEASE LEDGER: PASS tuple=port/framework/generation one-shot=external")
