#!/usr/bin/env python3
"""Prepare and bundle one frozen, data-free Tearscape V4 candidate."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


PORT = Path(__file__).resolve().parent.parent
PORT_ID = "tearscape"
LAUNCHER = "Tearscape.sh"
ENGINE = "tearscape-nextos"
FRAMEWORK_COMMIT = "4db2fff34ff1c6cd4019754d230f036c81834a2e"
# component-tag-pinned: an immutable annotated tag names the nxinput release.
# candidate-commit-pinned (mission 11): NO tag exists or may be created; the
# pin is the exact commit + component VERSION, and the artifact is a frozen
# CANDIDATE that can never claim physical support, publish or promote.
FRAMEWORK_STATE = "candidate-commit-pinned"
NXINPUT_COMPONENT_VERSION = "0.10.2"
NXINPUT_FRAMEWORK_TAG = "nxinput-v0.9.0"
NXINPUT_FRAMEWORK_TAG_OBJECT = "32ed6553e16bc4195baed988ea9a9d3849fd86b1"
NXINPUT_FRAMEWORK_TAG_OBJECT_SHA256 = \
    "2accbb023971fa47c4e73d23ee2e46472572b857f94911cabca3d9cf814c95b2"
NXINPUT_0_9_PIN_PENDING = False
SOURCE_DATE_EPOCH = 1771254831
NUPKG_SHA256 = "051bfc73f47f7d28bf9fb4cee616df71063ffc3da8861ec4f0645b0e73476d78"
PUBLIC_CEILING = (2, 30)
PREPARED_SCHEMA = "org.nextos.tearscape.release-preparation"
BUNDLE_ATTEMPT_SCHEMA = "org.nextos.tearscape.bundle-attempt"
BUNDLE_ATTEMPT_FILE = "BUNDLE-ATTEMPT.json"
ATTEMPT_LEDGER_SCHEMA = "org.nextos.release-attempt-ledger"
GENERATION_ID = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{64})$")
RENAME_NOREPLACE = 1
SOURCE_URL = re.compile(
    r"^https://github[.]com/NextOs-Ports/nextos_ports_android/tree/([0-9a-f]{40})$"
)
TEXT_SUFFIXES = frozenset((
    ".cfg", ".gptk", ".json", ".md", ".py", ".sh", ".txt", ".xml",
    ".sha256",
))
SOURCE_NAME_TOKENS = ("apkpure", "apkmirror", "apkvision", "5play")
PERSONAL_PATH = re.compile(r"/home/[^/\s\"']+|/root(?:/|\b)|/mnt/ARQUIVOS(?:/|\b)", re.I)
IPV4 = re.compile(r"(?<![0-9])(?:[0-9]{1,3}[.]){3}[0-9]{1,3}(?![0-9])")
PUBLIC_VERSION_LITERALS = frozenset(("4.6.1.0",))
GLIBC_BASE_SONAMES = frozenset((
    "libanl.so.1", "libBrokenLocale.so.1", "libc.so.6", "libdl.so.2",
    "libm.so.6", "libnsl.so.1", "libpthread.so.0", "libresolv.so.2",
    "librt.so.1", "libutil.so.1",
))
FIRMWARE_BASELINE_SONAMES = frozenset((
    "ld-linux-aarch64.so.1", "libEGL.so", "libEGL.so.1",
    "libGLESv2.so", "libGLESv2.so.2", "libGLESv1_CM.so",
    "libGLESv1_CM.so.1", "libgcc_s.so.1", "libstdc++.so.6",
    "libz.so.1", "libfreetype.so.6", "libopenal.so.1",
))
PORTMASTER_BASELINE_SONAMES = frozenset((
    "libSDL2-2.0.so.0", "libSDL2_mixer-2.0.so.0",
    "libSDL2_image-2.0.so.0", "libSDL2_ttf-2.0.so.0",
    "libSDL2_net-2.0.so.0", "libSDL2_gfx-1.0.so.0",
))
ROOT_FILES = {
    "adapter/adapter-contract.json": 0o644,
    "BUILD-PROVENANCE.json": 0o644,
    "controllers.nxb": 0o644,
    "controllers-modern.nxb": 0o644,
    "controllers-retro.nxb": 0o644,
    "FRAMEWORK-PIN.json": 0o644,
    "INSTALLATION.md": 0o644,
    "LICENSE": 0o644,
    "NOTICE.md": 0o644,
    "PROJECT-BUILD-PIN.json": 0o644,
    "README.md": 0o644,
    "extractor.json": 0o644,
    "nxextract/patch-camera-touch-thresholds.py": 0o644,
    "nxproject.json": 0o644,
    "port-env.sh": 0o644,
    "version.txt": 0o644,
}


class PipelineError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise PipelineError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"{label} is unavailable: {error}")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        fail(f"{label} must be a regular non-symlink file: {path}")
    return path


def directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as error:
        fail(f"{label} is unavailable: {error}")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        fail(f"{label} must be a real directory: {path}")
    return path


def strict_json(path: Path, label: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail(f"{label} repeats JSON key {key!r}")
            result[key] = value
        return result
    try:
        value = json.loads(regular(path, label).read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"cannot parse {label}: {error}")
    if not isinstance(value, dict):
        fail(f"{label} must be a JSON object")
    return value


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def run(arguments: list[str], *, cwd: Path | None = None, timeout: int = 1800,
        emit: bool = True) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "LC_ALL": "C"})
    try:
        result = subprocess.run(
            arguments, cwd=str(cwd) if cwd else None, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        fail(f"cannot run {arguments[0]}: {error}")
    if emit and result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode:
        fail(f"command failed with status {result.returncode}: {' '.join(arguments[:2])}")
    return result


def copy_regular(source: Path, destination: Path, mode: int) -> None:
    regular(source, f"allowlisted source {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        fail(f"no-overwrite staging collision: {destination}")
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def git_output(repository: Path, *arguments: str) -> str:
    return run(["git", "-C", str(repository), *arguments], emit=False).stdout.strip()


def verify_clean_inputs(framework: Path, source_url: str) -> str:
    match = SOURCE_URL.fullmatch(source_url)
    if not match:
        fail("--source-url must pin this repository to one immutable 40-hex commit")
    framework = directory(framework, "Framework V4 checkout").resolve()
    if git_output(framework, "rev-parse", "HEAD") != FRAMEWORK_COMMIT:
        fail("Framework V4 checkout is not the pinned integration commit")
    if git_output(framework, "status", "--porcelain", "--untracked-files=normal"):
        fail("Framework V4 checkout is dirty")
    if FRAMEWORK_STATE == "component-tag-pinned":
        tag_ref = f"refs/tags/{NXINPUT_FRAMEWORK_TAG}"
        if git_output(framework, "cat-file", "-t", tag_ref) != "tag":
            fail("nxinput component release is not an annotated tag")
        if git_output(framework, "rev-parse", tag_ref) != NXINPUT_FRAMEWORK_TAG_OBJECT:
            fail("nxinput component tag object differs from the immutable pin")
        tag_payload = subprocess.run(
            ["git", "-C", str(framework), "cat-file", "tag", tag_ref],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False,
        )
        logical_tag_object = (
            b"tag " + str(len(tag_payload.stdout)).encode() + b"\0" + tag_payload.stdout
        )
        if (tag_payload.returncode != 0 or hashlib.sha256(
                logical_tag_object).hexdigest() != NXINPUT_FRAMEWORK_TAG_OBJECT_SHA256):
            fail("nxinput component tag object differs from its SHA-256 pin")
        if git_output(framework, "rev-parse", f"{tag_ref}^{{commit}}") != FRAMEWORK_COMMIT:
            fail("nxinput component tag does not resolve to the pinned commit")
    elif FRAMEWORK_STATE == "candidate-commit-pinned":
        # A candidate has NO tag and may never gain one from here. Identity is
        # the exact commit and the released component VERSION; the candidate
        # can never claim physical support or be promoted/published, and the
        # refusal below is deliberately fail-closed.
        version = git_output(
            framework, "show", f"{FRAMEWORK_COMMIT}:framework/nxinput/VERSION")
        if version.strip() != NXINPUT_COMPONENT_VERSION:
            fail("candidate pin: released nxinput VERSION mismatch")
        project = json.loads((PORT / "nxproject.json").read_text("utf-8"))
        support = project.get("documentation", {})
        if support.get("proven_support"):
            fail("a candidate-commit-pinned build must not claim proven support")
    else:
        fail(f"unknown framework pin state: {FRAMEWORK_STATE}")
    repository = Path(git_output(PORT, "rev-parse", "--show-toplevel"))
    head = git_output(repository, "rev-parse", "HEAD")
    if match.group(1) != head:
        fail("source URL does not name the frozen port HEAD")
    if git_output(repository, "status", "--porcelain", "--untracked-files=normal"):
        fail("port checkout is dirty")
    return head


def load_python_module(path: Path, name: str):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_refresh_module():
    return load_python_module(
        PORT / "recipes/refresh_release_inputs.py", "tearscape_refresh",
    )


def release_artifact(base: Path, manifest_relative: str) -> Path:
    manifest = strict_json(base / manifest_relative, "framework release manifest")
    item = manifest.get("artifacts", {}).get("aarch64", {})
    relative = item.get("path")
    if not isinstance(relative, str) or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
        fail("framework release manifest has an unsafe AArch64 artifact")
    return regular(base / PurePosixPath(relative), "framework AArch64 artifact")


def stage_source_root(
    port: Path, framework: Path, engine_dir: Path, nupkg: Path,
    destination: Path,
) -> tuple[Path, list[dict], str]:
    destination.mkdir(mode=0o755)
    for relative, mode in ROOT_FILES.items():
        copy_regular(port / relative, destination / relative, mode)
    license_root = directory(port / "licenses", "license directory")
    for source in sorted(license_root.iterdir()):
        if source.is_file() and not source.is_symlink():
            copy_regular(source, destination / "licenses" / source.name, 0o644)
    copy_regular(engine_dir / ENGINE, destination / ENGINE, 0o755)
    copy_regular(engine_dir / "shimlib/libEGL.so", destination / "lib/libEGL.so", 0o755)
    copy_regular(engine_dir / "shimlib/libGLESv2.so", destination / "lib/libGLESv2.so", 0o755)
    nxextract = framework / "suportando_outros_devices/extrator-universal"
    sources = {
        "nxextract/nxextract.py": (nxextract / "nxextract.py", 0o644),
        "nxextract/run-extractor.sh": (nxextract / "run-extractor.sh", 0o644),
        "nxextract/nxextract-runtime-env.sh": (nxextract / "nxextract-runtime-env.sh", 0o644),
        "nxextract/nxextract-ui": (release_artifact(nxextract, "ui/release/manifest-v1.json"), 0o755),
        "nxsplash-nextos": (release_artifact(framework / "framework/nxsplash", "release/manifest-v1.json"), 0o755),
    }
    for relative, (source, mode) in sources.items():
        copy_regular(source, destination / PurePosixPath(relative), mode)
    runtime_records, runtime_max = materialize_runtime(destination, nupkg)
    project = strict_json(destination / "nxproject.json", "staged nxproject")
    closure = project.get("nxport", {}).get("generation_runtime")
    if not isinstance(closure, list) or not closure:
        fail("nxproject lacks the generation-v2 runtime closure")
    for record in closure:
        path = destination / PurePosixPath(record["path"])
        if sha256_file(regular(path, record["path"])) != record["sha256"]:
            fail(f"staged generation member differs: {record['path']}")
        if stat.S_IMODE(path.stat().st_mode) != int(record["mode"], 8):
            fail(f"staged generation member mode differs: {record['path']}")
    print(f"SOURCE ROOT: PASS generation_members={len(closure)}")
    return destination, runtime_records, runtime_max


def gate_launcher(launcher: Path, work: Path) -> None:
    fixture = work / "launcher-no-stat-gate"
    fixture.mkdir(mode=0o755)
    test_launcher = fixture / LAUNCHER
    copy_regular(launcher, test_launcher, 0o755)
    fixture_bin = fixture / "bin"
    fixture_bin.mkdir(mode=0o755)
    for name in ("cut", "dirname", "grep", "readlink", "tr"):
        (fixture_bin / name).symlink_to(regular(Path("/usr/bin") / name, name))
    runtime = fixture / "runtime"
    runtime.mkdir(mode=0o700)
    finish_log = fixture / "finish.log"
    environment = {
        "PATH": str(fixture_bin), "XDG_RUNTIME_DIR": str(runtime),
        "TMPDIR": str(fixture), "LC_ALL": "C",
        "TEARSCAPE_PM_FINISH_LOG": str(finish_log),
        "BASH_FUNC_pm_finish%%": "() { printf '%s\\n' finish >> \"$TEARSCAPE_PM_FINISH_LOG\"; }",
    }
    process = subprocess.Popen(
        ["/bin/bash", str(test_launcher)], cwd=str(fixture), env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
    )
    try:
        output, _ = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill(); process.communicate(); fail("generated launcher gate timed out")
    if process.returncode == 0:
        fail("generated launcher unexpectedly passed the pre-runtime failure fixture")
    if not finish_log.is_file() or finish_log.read_text().splitlines() != ["finish"]:
        fail("generated launcher did not finalize PortMaster exactly once")
    logs = list(fixture.glob("tearscape-launcher-error.*.log"))
    if len(logs) != 1 or stat.S_IMODE(logs[0].stat().st_mode) != 0o600:
        fail("generated launcher did not create exactly one private early-error log")
    text = logs[0].read_text(encoding="utf-8")
    if not all(token in text for token in ("pre-runtime failure", f"status={process.returncode}", "pid=", "launcher=", "game_dir=", "cfw=")):
        fail("generated launcher early-error log is incomplete")
    if list(fixture.rglob("log.txt")):
        fail("generated launcher opened the normal log before runtime")
    print(f"LAUNCHER NO-STAT/EARLY-LOG: PASS status={process.returncode} output_bytes={len(output.encode())}")


def elf_metadata(path: Path) -> tuple[list[str], str | None, str]:
    header = run(["readelf", "-hW", str(path)], emit=False).stdout
    if "Machine:                           AArch64" not in header:
        fail(f"runtime ELF is not AArch64: {path.name}")
    dynamic = run(["readelf", "-dW", str(path)], emit=False).stdout
    if re.search(r"\((?:RPATH|RUNPATH|TEXTREL)\)", dynamic):
        fail(f"runtime ELF embeds RPATH, RUNPATH or TEXTREL: {path.name}")
    needed = sorted(re.findall(r"\(NEEDED\).*?\[([^]]+)\]", dynamic))
    sonames = re.findall(r"\(SONAME\).*?\[([^]]+)\]", dynamic)
    versions = [tuple(map(int, value.split("."))) for value in re.findall(r"GLIBC_([0-9]+(?:[.][0-9]+)+)", run(["readelf", "--version-info", str(path)], emit=False).stdout)]
    if not versions or max(versions) > PUBLIC_CEILING:
        fail(f"runtime ELF exceeds GLIBC 2.30 or lacks a GLIBC receipt: {path.name}")
    glibc = ".".join(map(str, max(versions)))
    return needed, sonames[0] if sonames else None, glibc


def materialize_runtime(root: Path, nupkg: Path) -> tuple[list[dict], str]:
    publish = root / "game/.godot/mono/publish/arm64"
    publish.mkdir(parents=True, mode=0o755)
    run([
        sys.executable, str(PORT / "recipes/materialize_dotnet_runtime.py"),
        "--nupkg", str(nupkg), "--manifest", str(PORT / "runtime/dotnet-runtime-manifest.json"),
        "--output", str(publish),
    ])
    copy_regular(PORT / "runtime/Tearscape.deps.json", publish / "Tearscape.deps.json", 0o644)
    copy_regular(PORT / "defaults/override.cfg", root / "game/override.cfg", 0o644)
    manifest = strict_json(PORT / "runtime/dotnet-runtime-manifest.json", ".NET runtime manifest")
    records = []
    native_max = (0, 0)
    for item in manifest["files"]:
        relative = f"game/.godot/mono/publish/arm64/{item['destination']}"
        source = regular(root / PurePosixPath(relative), relative)
        if source.stat().st_size != item["size"] or sha256_file(source) != item["sha256"]:
            fail(f"materialized .NET member differs: {item['destination']}")
        record = {
            "source": f"{PORT_ID}/{relative}", "target": f"{PORT_ID}/{relative}",
            "kind": "payload", "mode": "0644", "sha256": item["sha256"],
        }
        if item["kind"] == "native-linux":
            needed, soname, glibc = elf_metadata(source)
            native_max = max(native_max, tuple(map(int, glibc.split("."))))
            record.update({
                "kind": "third-party-linux", "architecture": "aarch64",
                "build_profile": "universal-low-glibc",
                "provenance": f"Microsoft.NETCore.App.Runtime.linux-arm64 10.0.3 nupkg sha256:{NUPKG_SHA256}",
                "needed": needed, "soname": soname,
            })
        records.append(record)
    relative = "game/.godot/mono/publish/arm64/Tearscape.deps.json"
    records.append({
        "source": f"{PORT_ID}/{relative}", "target": f"{PORT_ID}/{relative}",
        "kind": "payload", "mode": "0644", "sha256": sha256_file(publish / "Tearscape.deps.json"),
    })
    return records, ".".join(map(str, native_max))


def provider_for(soname: str) -> str:
    if soname in GLIBC_BASE_SONAMES:
        return "glibc-base"
    if soname in FIRMWARE_BASELINE_SONAMES:
        return "firmware"
    if soname in PORTMASTER_BASELINE_SONAMES:
        return "portmaster"
    fail(f"unbundled dependency is outside the universal baseline: {soname}")


def patch_manifest(path: Path, runtime_records: list[dict], project_pin: dict) -> None:
    manifest = strict_json(path, "rendered nxrelease manifest")
    files = manifest.get("files")
    if not isinstance(files, list):
        fail("rendered manifest lacks a file inventory")
    by_target = {}
    for record in files:
        target = record.get("target")
        if not isinstance(target, str) or target in by_target:
            fail(f"rendered manifest has an invalid or duplicate target: {target}")
        by_target[target] = record
    adapter_target = f"{PORT_ID}/adapter/adapter-contract.json"
    if adapter_target not in by_target:
        fail("rendered manifest omits the promoted adapter contract")
    linux_metadata = (
        "architecture", "build_profile", "provenance", "needed", "soname",
    )
    generation_prefix = f"{PORT_ID}/.nxruntime/generations/"
    for expected in runtime_records:
        target = expected["target"]
        rendered = by_target.get(target)
        if rendered is None:
            fail(f"generated runtime member is absent from the manifest: {target}")
        for field in ("source", "target", "mode", "sha256"):
            if rendered.get(field) != expected.get(field):
                fail(f"generated runtime {field} differs: {target}")
        logical = target.removeprefix(f"{PORT_ID}/")
        stores = [
            record for record in files
            if record["target"].startswith(generation_prefix) and
            record["target"].endswith("/files/runtime/" + logical)
        ]
        if len(stores) != 1:
            fail(f"generated runtime lacks one immutable counterpart: {target}")
        store = stores[0]
        if (store.get("mode") != expected["mode"] or
                store.get("sha256") != expected["sha256"]):
            fail(f"immutable runtime differs from its live member: {target}")
        if expected["kind"] == "third-party-linux":
            if (rendered.get("kind") != "third-party-linux" or
                    store.get("kind") != "nxruntime-generation-linux"):
                fail(f"native runtime was not classified as Linux: {target}")
            for field in ("architecture", "build_profile", "needed", "soname"):
                if rendered.get(field) != expected.get(field):
                    fail(f"native runtime {field} differs: {target}")
            rendered["provenance"] = expected["provenance"]
            for field in linux_metadata:
                store[field] = rendered[field]
        else:
            if (rendered.get("kind") != "payload" or
                    store.get("kind") != "nxruntime-generation"):
                fail(f"managed runtime was not classified as payload: {target}")
            if any(field in rendered or field in store for field in linux_metadata):
                fail(f"managed runtime gained Linux metadata: {target}")
    output_targets = {
        f"{PORT_ID}/{ENGINE}": ENGINE,
        f"{PORT_ID}/lib/libEGL.so": "libEGL.so",
        f"{PORT_ID}/lib/libGLESv2.so": "libGLESv2.so",
    }
    live_provenance = {
        ENGINE: "Godot 4.6.1 Mono plus Tearscape Linux/fbdev, Framework V4 nxinput 0.9.0 live seams and nxgl 0.3.4 fatal frame proof; frozen-input offline Arm GNU 10.3 Buster build",
        "libEGL.so": "Tearscape EGL shim; frozen-input offline Arm GNU 10.3 Buster build",
        "libGLESv2.so": "Tearscape GLES2 translation shim; frozen-input offline Arm GNU 10.3 Buster build",
    }
    for record in files:
        target = record.get("target")
        name = output_targets.get(target)
        if name:
            pinned = project_pin["outputs"][name]
            if record.get("sha256") != pinned["sha256"]:
                fail(f"rendered project output differs from its pin: {name}")
            record["kind"] = "project-linux"
            record["provenance"] = live_provenance[name]
        if isinstance(target, str) and target.startswith(f"{PORT_ID}/.nxruntime/generations/"):
            for live_target, output_name in output_targets.items():
                logical = live_target.removeprefix(f"{PORT_ID}/")
                if target.endswith("/files/runtime/" + logical):
                    pinned = project_pin["outputs"][output_name]
                    if record.get("sha256") != pinned["sha256"]:
                        fail(f"generation output differs from its pin: {output_name}")
                    record["provenance"] = live_provenance[output_name]
    files.sort(key=lambda record: record["target"])
    needed = sorted({name for record in files for name in record.get("needed", [])})
    bundled = {
        record.get("soname"): record["target"] for record in files
        if record.get("kind") in ("project-linux", "third-party-linux")
        and record.get("soname")
    }
    manifest["dependencies"] = [
        ({"namespace": "linux", "architecture": "aarch64", "soname": name,
          "provider": "package", "path": bundled[name]}
         if name in bundled else
         {"namespace": "linux", "architecture": "aarch64", "soname": name,
          "provider": provider_for(name)})
        for name in needed
    ]
    path.write_bytes(canonical_json(manifest))


def scan_public_text(label: str, payload: bytes, shell_stat_detector) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(f"public text is not UTF-8 ({label}): {error}")
    folded = text.casefold()
    if any(token in folded for token in SOURCE_NAME_TOKENS):
        fail(f"public text reveals an APK download source: {label}")
    ip_literals = {match.group(0) for match in IPV4.finditer(text)}
    allowed_ip_literals = (
        PUBLIC_VERSION_LITERALS
        if PurePosixPath(label).name == "Tearscape.deps.json" else frozenset()
    )
    if PERSONAL_PATH.search(text) or (ip_literals - allowed_ip_literals):
        fail(f"public text contains a private path or IP: {label}")
    if label.endswith(".sh") and shell_stat_detector(text, label):
        fail(f"public shell invokes external stat: {label}")


def verify_bundle(bundle: Path, shell_stat_detector) -> tuple[Path, str]:
    expected = {"tearscape.zip", "tearscape.zip.sha256", "BUILD-PROVENANCE.json"}
    if {item.name for item in bundle.iterdir()} != expected:
        fail("NXRelease bundle does not contain exactly ZIP, checksum and provenance")
    archive = regular(bundle / "tearscape.zip", "final ZIP")
    digest = sha256_file(archive)
    if (bundle / "tearscape.zip.sha256").read_text() != f"{digest}  tearscape.zip\n":
        fail("final ZIP checksum file is stale")
    with zipfile.ZipFile(archive, "r") as zipped:
        names = zipped.namelist()
        if names != sorted(names) or len(names) != len(set(names)):
            fail("final ZIP inventory is not sorted and unique")
        required = {
            LAUNCHER, f"{PORT_ID}/{ENGINE}", f"{PORT_ID}/INSTALLATION.md",
            f"{PORT_ID}/adapter/adapter-contract.json",
            f"{PORT_ID}/extractor.json", f"{PORT_ID}/nxextract/nxextract.py",
            f"{PORT_ID}/nxsplash-nextos", f"{PORT_ID}/FRAMEWORK-PIN.json",
            f"{PORT_ID}/controllers.nxb",
            f"{PORT_ID}/controllers-modern.nxb",
            f"{PORT_ID}/controllers-retro.nxb",
            f"{PORT_ID}/PROJECT-BUILD-PIN.json", f"{PORT_ID}/BUILD-PROVENANCE.json",
            f"{PORT_ID}/game/override.cfg",
            f"{PORT_ID}/game/.godot/mono/publish/arm64/System.Private.CoreLib.dll",
            f"{PORT_ID}/game/.godot/mono/publish/arm64/libcoreclr.so",
            f"{PORT_ID}/game/.godot/mono/publish/arm64/Tearscape.deps.json",
        }
        if not required.issubset(names):
            fail(f"final ZIP omits required members: {sorted(required - set(names))}")
        forbidden_names = ("Tearscape.dll", "GodotSharp.dll", ".apk", ".apkm", ".apks", ".xapk")
        for info in zipped.infolist():
            if any(info.filename.endswith(name) for name in forbidden_names):
                fail(f"owner game data entered the public ZIP: {info.filename}")
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type != stat.S_IFREG:
                fail(f"non-regular member entered the public ZIP: {info.filename}")
            payload = zipped.read(info)
            logical = PurePosixPath(info.filename)
            first = payload.splitlines()[0].decode("utf-8", "ignore") if payload else ""
            if (logical.suffix.casefold() in TEXT_SUFFIXES or
                    logical.name in ("LICENSE", "controllers.nxb",
                                     "controllers-modern.nxb",
                                     "controllers-retro.nxb") or
                    first.startswith("#!")):
                scan_public_text(info.filename, payload, shell_stat_detector)
    print(f"PUBLIC ARCHIVE AUDIT: PASS sha256={digest} owner_data=absent symlinks=absent")
    return archive, digest


def rename_noreplace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        fail("renameat2 is required for no-overwrite bundle publication")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(
            -100, os.fsencode(source), -100, os.fsencode(destination),
            RENAME_NOREPLACE) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            fail(f"publication destination already exists: {destination}")
        fail(f"cannot publish without overwrite: {os.strerror(error)}")
    parent_descriptor = os.open(
        destination.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)


def absolute_without_following(path: Path) -> Path:
    """Make a pathname absolute without resolving its final directory entry."""
    return Path(os.path.abspath(os.fspath(path)))


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def external_output(path: Path, label: str) -> tuple[Path, Path]:
    if not path.name or path.name in (".", ".."):
        fail(f"{label} path is invalid")
    parent_path = path.parent.resolve()
    if parent_path == Path("/"):
        fail(f"{label} parent cannot be the filesystem root")
    parent = directory(parent_path, f"{label} parent")
    output = parent / path.name
    if output.exists() or output.is_symlink():
        fail(f"{label} already exists: {output}")
    repository = Path(git_output(PORT, "rev-parse", "--show-toplevel")).resolve()
    if is_within(output, repository):
        fail(f"{label} must be external to the port checkout")
    return parent, output


def tree_identity(root: Path) -> dict:
    """Hash every path/type/mode/size/content in one generated scaffold."""
    root = directory(root, "prepared scaffold")
    records = []
    file_count = 0
    directory_count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if metadata.st_uid != os.geteuid():
            fail(f"prepared scaffold member has a foreign owner: {relative}")
        mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
        if stat.S_ISDIR(metadata.st_mode):
            directory_count += 1
            records.append({"kind": "directory", "mode": mode, "path": relative})
        elif stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                fail(f"prepared scaffold member is hardlinked: {relative}")
            file_count += 1
            records.append({
                "kind": "file", "mode": mode, "path": relative,
                "sha256": sha256_file(path), "size": metadata.st_size,
            })
        else:
            fail(f"prepared scaffold has a symlink or special member: {relative}")
    payload = (json.dumps(
        records, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("utf-8")
    return {
        "directory_count": directory_count,
        "file_count": file_count,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def write_prepared_receipt(path: Path, receipt: dict) -> None:
    if path.exists() or path.is_symlink():
        fail(f"prepared receipt already exists: {path}")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
    )
    try:
        payload = canonical_json(receipt)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, 0o444)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or
                metadata.st_uid != os.geteuid() or metadata.st_nlink != 1 or
                stat.S_IMODE(metadata.st_mode) != 0o444):
            fail("prepared receipt did not freeze as an owned single-link 0444 file")
    finally:
        os.close(descriptor)


def attempt_ledger_id(receipt: dict) -> str:
    identity = {
        "framework_commit": receipt["framework_commit"],
        "generation": receipt["generation"],
        "port_commit": receipt["port_commit"],
        "port_id": receipt["port_id"],
    }
    return hashlib.sha256(canonical_json(identity)).hexdigest()


def attempt_ledger_payload(
        receipt: dict, lock_sha256: str, prepared_receipt_sha256: str) -> dict:
    return {
        "candidate_lock_sha256": lock_sha256,
        "framework_commit": receipt["framework_commit"],
        "generation": receipt["generation"],
        "port_commit": receipt["port_commit"],
        "port_id": receipt["port_id"],
        "prepared_receipt_sha256": prepared_receipt_sha256,
        "scaffold_sha256": receipt["scaffold"]["sha256"],
        "schema": ATTEMPT_LEDGER_SCHEMA,
        "schema_version": 1,
    }


def canonical_attempt_ledger_root() -> Path:
    """Return the sole machine-local ledger; callers cannot choose another."""
    account_home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    home = directory(account_home, "account home for attempt ledger").resolve()
    current = home
    for component in (".local", "state", "nextos-release-attempts", PORT_ID):
        current = current / component
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            pass
        metadata = current.lstat()
        if (stat.S_ISLNK(metadata.st_mode) or
                not stat.S_ISDIR(metadata.st_mode) or
                metadata.st_uid != os.geteuid()):
            fail(f"attempt ledger path is unsafe: {current}")
    metadata = current.lstat()
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        fail("canonical attempt ledger must have mode 0700")
    return current


def claim_external_bundle_attempt_at(
        root: Path, receipt: dict, lock_sha256: str,
        prepared_receipt_sha256: str) -> tuple[str, str]:
    root = directory(root, "attempt ledger root")
    metadata = root.lstat()
    if (metadata.st_uid != os.geteuid() or
            stat.S_IMODE(metadata.st_mode) != 0o700):
        fail("attempt ledger root must be owned at mode 0700")
    ledger_id = attempt_ledger_id(receipt)
    ledger_path = root / f"{ledger_id}.json"
    if ledger_path.exists() or ledger_path.is_symlink():
        fail(
            "nxrelease was already attempted for this "
            "port/framework/generation tuple"
        )
    payload = attempt_ledger_payload(
        receipt, lock_sha256, prepared_receipt_sha256,
    )
    write_prepared_receipt(ledger_path, payload)
    descriptor = os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return ledger_id, sha256_file(ledger_path)


def claim_external_bundle_attempt(
        prepared: Path, receipt: dict, lock_sha256: str) -> tuple[str, str]:
    return claim_external_bundle_attempt_at(
        canonical_attempt_ledger_root(), receipt, lock_sha256,
        sha256_file(prepared / "PREPARED.json"),
    )


def verify_external_bundle_attempt(
        prepared: Path, receipt: dict, attempt: dict) -> None:
    ledger_id = attempt.get("ledger_id")
    if (not isinstance(ledger_id, str) or
            ledger_id != attempt_ledger_id(receipt)):
        fail("bundle attempt ledger identity differs from the candidate")
    ledger_path = canonical_attempt_ledger_root() / f"{ledger_id}.json"
    metadata = regular(ledger_path, "canonical bundle attempt ledger").lstat()
    if (metadata.st_uid != os.geteuid() or metadata.st_nlink != 1 or
            stat.S_IMODE(metadata.st_mode) != 0o444):
        fail("canonical bundle attempt ledger must be owned, single-link and 0444")
    ledger_sha256 = attempt.get("ledger_sha256")
    if (not isinstance(ledger_sha256, str) or
            ledger_sha256 != sha256_file(ledger_path)):
        fail("canonical bundle attempt ledger digest differs")
    expected = attempt_ledger_payload(
        receipt, attempt["candidate_lock_sha256"],
        sha256_file(prepared / "PREPARED.json"),
    )
    if strict_json(ledger_path, "canonical bundle attempt ledger") != expected:
        fail("canonical bundle attempt ledger payload differs")


def verify_prepared_root(path: Path) -> dict:
    """Recompute the persistent preparation identity without regenerating it."""
    prepared = directory(absolute_without_following(path), "prepared root")
    repository = Path(git_output(PORT, "rev-parse", "--show-toplevel")).resolve()
    if is_within(prepared, repository):
        fail("prepared root must be external to the port checkout")
    names = {item.name for item in prepared.iterdir()}
    # nxgenerator >= 0.3.16 writes the ON_DEVICE_AUTOMATED_INPUT_PROOF roteiros
    # beside the scaffold ("scaffold-proof"); they never enter the ZIP.
    pristine_names = {"PREPARED.json", "scaffold", "scaffold-proof"}
    attempted_names = pristine_names | {BUNDLE_ATTEMPT_FILE}
    if names not in (pristine_names, attempted_names):
        fail("prepared root has an unexpected inventory")
    attempted = BUNDLE_ATTEMPT_FILE in names
    prepared_metadata = prepared.lstat()
    expected_mode = 0o500 if attempted else 0o700
    if (prepared_metadata.st_uid != os.geteuid() or
            stat.S_IMODE(prepared_metadata.st_mode) != expected_mode):
        fail(f"prepared root must be owned at mode {expected_mode:04o}")
    receipt_path = regular(prepared / "PREPARED.json", "prepared receipt")
    receipt_metadata = receipt_path.lstat()
    if (receipt_metadata.st_uid != os.geteuid() or
            receipt_metadata.st_nlink != 1 or
            stat.S_IMODE(receipt_metadata.st_mode) != 0o444):
        fail("prepared receipt must be an owned single-link 0444 file")
    receipt = strict_json(receipt_path, "prepared receipt")
    expected_fields = {
        "engine_sha256", "framework_commit", "generation", "manifest_sha256",
        "nupkg_sha256", "port_commit", "port_id", "runtime_max_glibc",
        "scaffold", "schema", "schema_version", "source_date_epoch",
        "source_url",
    }
    if set(receipt) != expected_fields:
        fail("prepared receipt fields are not canonical")
    if (receipt.get("schema") != PREPARED_SCHEMA or
            type(receipt.get("schema_version")) is not int or
            receipt.get("schema_version") != 1 or
            receipt.get("port_id") != PORT_ID or
            receipt.get("framework_commit") != FRAMEWORK_COMMIT or
            receipt.get("source_date_epoch") != SOURCE_DATE_EPOCH or
            receipt.get("nupkg_sha256") != NUPKG_SHA256):
        fail("prepared receipt identity differs from this pipeline")
    source_match = SOURCE_URL.fullmatch(receipt.get("source_url", ""))
    if source_match is None or source_match.group(1) != receipt.get("port_commit"):
        fail("prepared receipt source URL and port commit differ")
    generation = receipt.get("generation")
    if not isinstance(generation, str) or GENERATION_ID.fullmatch(generation) is None:
        fail("prepared receipt generation is not canonical")
    for field in ("engine_sha256", "manifest_sha256"):
        value = receipt.get(field)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            fail(f"prepared receipt {field} is not a SHA-256")
    if (not isinstance(receipt.get("runtime_max_glibc"), str) or
            re.fullmatch(r"[0-9]+[.][0-9]+", receipt["runtime_max_glibc"]) is None):
        fail("prepared receipt runtime_max_glibc is not canonical")
    expected_scaffold = receipt.get("scaffold")
    if (not isinstance(expected_scaffold, dict) or
            set(expected_scaffold) != {"directory_count", "file_count", "sha256"} or
            type(expected_scaffold.get("directory_count")) is not int or
            type(expected_scaffold.get("file_count")) is not int or
            not isinstance(expected_scaffold.get("sha256"), str) or
            re.fullmatch(r"[0-9a-f]{64}", expected_scaffold["sha256"]) is None):
        fail("prepared receipt scaffold identity is malformed")
    scaffold = directory(prepared / "scaffold", "prepared scaffold")
    if tree_identity(scaffold) != expected_scaffold:
        fail("prepared scaffold identity drifted")
    manifest = regular(scaffold / "nxrelease.json", "prepared release manifest")
    if sha256_file(manifest) != receipt["manifest_sha256"]:
        fail("prepared release manifest drifted")
    engine = regular(scaffold / PORT_ID / ENGINE, "prepared Tearscape engine")
    if sha256_file(engine) != receipt["engine_sha256"]:
        fail("prepared Tearscape engine drifted")
    generation_receipt = strict_json(
        scaffold / PORT_ID / "GENERATION.json", "prepared generation receipt",
    )
    if generation_receipt.get("generation_id") != generation:
        fail("prepared generation receipt drifted")
    if attempted:
        attempt_path = regular(
            prepared / BUNDLE_ATTEMPT_FILE, "bundle attempt receipt",
        )
        attempt_metadata = attempt_path.lstat()
        if (attempt_metadata.st_uid != os.geteuid() or
                attempt_metadata.st_nlink != 1 or
                stat.S_IMODE(attempt_metadata.st_mode) != 0o444):
            fail("bundle attempt receipt must be owned, single-link and 0444")
        attempt = strict_json(attempt_path, "bundle attempt receipt")
        if set(attempt) != {
                "candidate_lock_sha256", "framework_commit", "generation",
                "ledger_id", "ledger_sha256", "port_commit",
                "prepared_receipt_sha256", "scaffold_sha256", "schema",
                "schema_version"}:
            fail("bundle attempt receipt fields are not canonical")
        if (attempt.get("schema") != BUNDLE_ATTEMPT_SCHEMA or
                type(attempt.get("schema_version")) is not int or
                attempt.get("schema_version") != 1 or
                attempt.get("framework_commit") != receipt["framework_commit"] or
                attempt.get("generation") != receipt["generation"] or
                attempt.get("port_commit") != receipt["port_commit"] or
                attempt.get("prepared_receipt_sha256") != sha256_file(receipt_path) or
                attempt.get("scaffold_sha256") != receipt["scaffold"]["sha256"] or
                not isinstance(attempt.get("candidate_lock_sha256"), str) or
                re.fullmatch(r"[0-9a-f]{64}", attempt["candidate_lock_sha256"]) is None):
            fail("bundle attempt receipt differs from the prepared candidate")
        verify_external_bundle_attempt(prepared, receipt, attempt)
    return receipt


def claim_bundle_attempt(prepared: Path, receipt: dict, lock_sha256: str) -> None:
    """Consume one prepared tree before the sole nxrelease invocation."""
    attempt_path = prepared / BUNDLE_ATTEMPT_FILE
    if attempt_path.exists() or attempt_path.is_symlink():
        fail("prepared candidate already has a bundle attempt")
    ledger_id, ledger_sha256 = claim_external_bundle_attempt(
        prepared, receipt, lock_sha256,
    )
    attempt = {
        "candidate_lock_sha256": lock_sha256,
        "framework_commit": receipt["framework_commit"],
        "generation": receipt["generation"],
        "ledger_id": ledger_id,
        "ledger_sha256": ledger_sha256,
        "port_commit": receipt["port_commit"],
        "prepared_receipt_sha256": sha256_file(prepared / "PREPARED.json"),
        "scaffold_sha256": receipt["scaffold"]["sha256"],
        "schema": BUNDLE_ATTEMPT_SCHEMA,
        "schema_version": 1,
    }
    write_prepared_receipt(attempt_path, attempt)
    descriptor = os.open(
        prepared, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        os.fchmod(descriptor, 0o500)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    verify_prepared_root(prepared)


def frozen_candidate_lock(path: Path, prepared: Path) -> tuple[Path, tuple]:
    candidate = regular(
        absolute_without_following(path), "external candidate lock",
    )
    metadata = candidate.lstat()
    if (metadata.st_uid != os.geteuid() or metadata.st_nlink != 1 or
            stat.S_IMODE(metadata.st_mode) != 0o444):
        fail("external candidate lock must be owned, single-link and exactly 0444")
    if is_within(candidate, prepared):
        fail("candidate lock must be external to the prepared root")
    repository = Path(git_output(PORT, "rev-parse", "--show-toplevel")).resolve()
    if is_within(candidate, repository):
        fail("candidate lock must be external to the port checkout")
    identity = (
        metadata.st_dev, metadata.st_ino, metadata.st_uid, metadata.st_nlink,
        metadata.st_mode, metadata.st_size, metadata.st_mtime_ns,
        metadata.st_ctime_ns, sha256_file(candidate),
    )
    return candidate, identity


def prepare_candidate(args: argparse.Namespace) -> int:
    framework = args.framework_root.resolve()
    engine_dir = directory(args.engine_dir.resolve(), "frozen engine directory")
    nupkg = regular(args.nupkg.resolve(), ".NET nupkg")
    if sha256_file(nupkg) != NUPKG_SHA256:
        fail(".NET nupkg differs from the pin")
    prepared_parent, prepared = external_output(args.prepared_root, "prepared root")
    if is_within(prepared, framework):
        fail("prepared root must be external to the framework checkout")
    port_commit = verify_clean_inputs(framework, args.source_url)
    run([sys.executable, str(PORT / "recipes/test_runtime_contract.py")])
    refresh = load_refresh_module()
    refresh.framework_pin(framework)
    run([
        sys.executable, str(PORT / "recipes/refresh_release_inputs.py"), "--check",
        "--framework-root", str(framework), "--engine-dir", str(engine_dir),
        "--nupkg", str(nupkg),
    ])
    project_pin = strict_json(PORT / "PROJECT-BUILD-PIN.json", "project build pin")
    temporary = Path(tempfile.mkdtemp(
        prefix=".tearscape-prepare.", dir=str(prepared_parent),
    ))
    try:
        source_root, runtime_records, runtime_max = stage_source_root(
            PORT, framework, engine_dir, nupkg, temporary / "source-root",
        )
        scaffold = temporary / "scaffold"
        generator = framework / "framework/nxgenerator/nxgenerator.py"
        renderer = framework / "framework/nxrelease/nx-render-manifest.py"
        run([
            sys.executable, str(generator), str(source_root / "nxproject.json"),
            "--source-root", str(source_root), "--output", str(scaffold),
        ])
        gate_root = temporary / "directed-gate"
        gate_root.mkdir(mode=0o700)
        gate_launcher(scaffold / LAUNCHER, gate_root)
        shutil.rmtree(gate_root)
        directory(scaffold / PORT_ID, "generated Tearscape directory")
        regular(
            scaffold / PORT_ID / "adapter/adapter-contract.json",
            "generated promoted adapter contract",
        )
        run([
            sys.executable, str(renderer), "--generator-root", str(scaffold),
            "--framework-root", str(framework / "framework"),
            "--source-url", args.source_url,
            "--source-date-epoch", str(SOURCE_DATE_EPOCH),
            "--max-glibc", "2.30", "--public-final",
        ])
        manifest_path = scaffold / "nxrelease.json"
        patch_manifest(manifest_path, runtime_records, project_pin)
        generation = strict_json(
            scaffold / PORT_ID / "GENERATION.json", "prepared generation receipt",
        ).get("generation_id")
        if not isinstance(generation, str) or GENERATION_ID.fullmatch(generation) is None:
            fail("generated scaffold lacks a canonical generation id")
        shutil.rmtree(source_root)
        receipt = {
            "engine_sha256": sha256_file(scaffold / PORT_ID / ENGINE),
            "framework_commit": FRAMEWORK_COMMIT,
            "generation": generation,
            "manifest_sha256": sha256_file(manifest_path),
            "nupkg_sha256": NUPKG_SHA256,
            "port_commit": port_commit,
            "port_id": PORT_ID,
            "runtime_max_glibc": runtime_max,
            "scaffold": tree_identity(scaffold),
            "schema": PREPARED_SCHEMA,
            "schema_version": 1,
            "source_date_epoch": SOURCE_DATE_EPOCH,
            "source_url": args.source_url,
        }
        write_prepared_receipt(temporary / "PREPARED.json", receipt)
        verify_prepared_root(temporary)
        verify_clean_inputs(framework, args.source_url)
        refresh.framework_pin(framework)
        rename_noreplace(temporary, prepared)
        verify_prepared_root(prepared)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    print(
        "TEARSCAPE PREPARE: PASS "
        f"prepared_root={prepared} generation={receipt['generation']} "
        f"files={receipt['scaffold']['file_count']}"
    )
    return 0


def bundle_candidate(args: argparse.Namespace) -> int:
    framework = args.framework_root.resolve()
    prepared = absolute_without_following(args.prepared_root)
    receipt = verify_prepared_root(prepared)
    if (prepared / BUNDLE_ATTEMPT_FILE).exists():
        fail("prepared candidate was already consumed by a bundle attempt")
    verify_clean_inputs(framework, receipt["source_url"])
    refresh = load_refresh_module()
    refresh.framework_pin(framework)
    candidate_lock, lock_identity = frozen_candidate_lock(
        args.candidate_lock, prepared,
    )
    destination_parent, destination = external_output(
        args.destination, "release destination",
    )
    if is_within(destination, prepared) or is_within(destination, framework):
        fail("release destination must be external to preparation and framework")
    scaffold = prepared / "scaffold"
    manifest_path = regular(
        scaffold / "nxrelease.json", "prepared release manifest",
    )
    release_tool = framework / "framework/nxrelease/nxrelease.py"
    release_module = load_python_module(release_tool, "tearscape_nxrelease")
    temporary = Path(tempfile.mkdtemp(
        prefix=".tearscape-bundle.", dir=str(destination_parent),
    ))
    try:
        temporary_bundle = temporary / "bundle"
        claim_bundle_attempt(prepared, receipt, lock_identity[-1])
        run([
            sys.executable, str(release_tool), "bundle",
            "--manifest", str(manifest_path),
            "--candidate-lock", str(candidate_lock),
            "--stage", str(temporary / "bundle-stage"),
            "--destination", str(temporary_bundle),
            "--archive-name", "tearscape.zip", "--max-glibc", "2.30",
        ])
        _archive, digest = verify_bundle(
            temporary_bundle, release_module.shell_invokes_external_stat,
        )
        verify_prepared_root(prepared)
        _lock, lock_identity_after = frozen_candidate_lock(
            candidate_lock, prepared,
        )
        if lock_identity_after != lock_identity:
            fail("external candidate lock changed during bundle")
        verify_clean_inputs(framework, receipt["source_url"])
        refresh.framework_pin(framework)
        rename_noreplace(temporary_bundle, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    print(
        "TEARSCAPE PUBLIC BYO RELEASE: PASS "
        f"bundle={destination} sha256={digest}"
    )
    return 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    phases = parser.add_subparsers(dest="phase", required=True)
    prepare = phases.add_parser(
        "prepare", help="generate and preserve one scaffold plus manifest",
    )
    prepare.add_argument("--framework-root", required=True, type=Path)
    prepare.add_argument("--engine-dir", required=True, type=Path)
    prepare.add_argument("--nupkg", required=True, type=Path)
    prepare.add_argument("--source-url", required=True)
    prepare.add_argument("--prepared-root", required=True, type=Path)
    prepare.set_defaults(handler=prepare_candidate)
    bundle = phases.add_parser(
        "bundle", help="bundle the already prepared scaffold exactly once",
    )
    bundle.add_argument("--framework-root", required=True, type=Path)
    bundle.add_argument("--prepared-root", required=True, type=Path)
    bundle.add_argument("--candidate-lock", required=True, type=Path)
    bundle.add_argument("--destination", required=True, type=Path)
    bundle.set_defaults(handler=bundle_candidate)
    return parser.parse_args()


def main() -> int:
    if NXINPUT_0_9_PIN_PENDING:
        fail("nxinput 0.9.0 awaits an immutable FRAMEWORK_COMMIT pin")
    args = parse_arguments()
    return args.handler(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PipelineError as error:
        print(f"TEARSCAPE PUBLIC BYO RELEASE: FAIL: {error}", file=sys.stderr)
        sys.exit(1)
