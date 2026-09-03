#!/usr/bin/env python3
"""Directed causal engine-receipt gate; no build, device, ZIP or network."""

import importlib.util
from pathlib import Path
import tempfile


PORT = Path(__file__).resolve().parents[2]
REFRESH_PATH = PORT / "recipes/refresh_release_inputs.py"
SPEC = importlib.util.spec_from_file_location("tearscape_refresh_receipt", REFRESH_PATH)
REFRESH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REFRESH)


def expect_failure(callable_, token: str) -> None:
    try:
        callable_()
    except SystemExit as error:
        assert token in str(error), str(error)
    else:
        raise AssertionError(f"receipt gate accepted {token}")


with tempfile.TemporaryDirectory(prefix="tearscape-engine-receipt-") as temporary:
    root = Path(temporary) / "port"
    engine = Path(temporary) / "engine"
    (root / "src").mkdir(parents=True)
    (root / "src/source.c").write_text("int tearscape_source;\n", encoding="utf-8")
    for index, relative in enumerate(REFRESH.BUILD_RECIPE_PATHS):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"recipe-{index}\n", encoding="utf-8")
        path.chmod(0o755 if relative.endswith(".sh") else 0o644)
    (engine / "shimlib").mkdir(parents=True)
    outputs = {
        REFRESH.ENGINE: b"engine-bytes",
        "shimlib/libEGL.so": b"egl-bytes",
        "shimlib/libGLESv2.so": b"gles-bytes",
    }
    for relative, payload in outputs.items():
        path = engine / relative
        path.write_bytes(payload)
        path.chmod(0o755)
    source_count, source_sha256 = REFRESH.tree_identity(root / "src")
    recipe_count, recipe_sha256 = REFRESH.build_recipe_identity(root)
    receipt = {
        "build": {
            "builder_image_id": "sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de",
            "clean_build_count": 1,
            "network": "none",
            "priority": "nice-10",
            "workers": 2,
        },
        "causal_inputs": {
            "recipe": {
                "files": recipe_count,
                "paths": list(REFRESH.BUILD_RECIPE_PATHS),
                "sha256": recipe_sha256,
            },
            "source_tree": {"files": source_count, "sha256": source_sha256},
        },
        "outputs": {
            relative: {
                "sha256": REFRESH.sha256_file(engine / relative),
                "size": (engine / relative).stat().st_size,
            }
            for relative in outputs
        },
        "port_head": "1" * 40,
        "port_id": "tearscape",
        "prepared_source_tree": {"files": 123, "sha256": "2" * 64},
        "schema": REFRESH.ENGINE_BUILD_RECEIPT_SCHEMA,
        "schema_version": 1,
        "source_date_epoch": REFRESH.SOURCE_DATE_EPOCH,
    }
    receipt_path = engine / REFRESH.ENGINE_BUILD_RECEIPT
    receipt_path.write_bytes(REFRESH.canonical_json(receipt))
    receipt_path.chmod(0o444)
    REFRESH.verify_engine_build_receipt(engine, port_root=root, verify_git=False)

    source = root / "src/source.c"
    original_source = source.read_bytes()
    source.write_bytes(b"changed source\n")
    expect_failure(
        lambda: REFRESH.verify_engine_build_receipt(
            engine, port_root=root, verify_git=False,
        ),
        "source/recipe bytes",
    )
    source.write_bytes(original_source)

    output = engine / REFRESH.ENGINE
    original_output = output.read_bytes()
    output.write_bytes(b"mixed old engine")
    expect_failure(
        lambda: REFRESH.verify_engine_build_receipt(
            engine, port_root=root, verify_git=False,
        ),
        "supplied engine",
    )
    output.write_bytes(original_output)

    receipt_path.chmod(0o644)
    expect_failure(
        lambda: REFRESH.verify_engine_build_receipt(
            engine, port_root=root, verify_git=False,
        ),
        "mode 0444",
    )

print("TEARSCAPE ENGINE BUILD RECEIPT: PASS source/recipe/output/mode fail-closed")
