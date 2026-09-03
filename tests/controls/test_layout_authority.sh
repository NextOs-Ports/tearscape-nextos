#!/bin/bash
# Tearscape 0.2.10 -- the muOS layout-authority port gates (mission 11.2).
#
# Directed, host-only, no device and no ZIP. Everything here is about the
# PORT's own bytes: the pre-init source order, the benign-zero declare
# contract, the three-bundle policy, the raw non-declaration, the quarantined
# generic fallback and the visual-source allowlist (mission 11.1).
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")" && pwd -P)
PORT=$(cd -- "$HERE/../.." && pwd -P)
JOYPAD="$PORT/src/godot_engine/v4-universal/drivers/sdl/joypad_sdl.cpp"
GLUE="$PORT/src/godot_engine/v4-universal/drivers/sdl/nxinput_gptk_godot.cpp"

fail() { printf 'tearscape-layout-authority FAILED: %s\n' "$1" >&2; exit 1; }

# --- 1. Source order: preinit < declare < SDL_Init (mission case 20/N24) ---
preinit_line=$(grep -n 'nxgptk_godot_preinit()' "$JOYPAD" | head -1 | cut -d: -f1)
declare_line=$(grep -n 'nxc6_declare_port_bundle_for_layout' "$JOYPAD" | head -1 | cut -d: -f1)
init_line=$(grep -n 'SDL_Init(' "$JOYPAD" | head -1 | cut -d: -f1)
[ -n "$preinit_line" ] && [ -n "$declare_line" ] && [ -n "$init_line" ] || \
  fail "the pre-init boundary chain is missing from joypad_sdl.cpp"
[ "$preinit_line" -lt "$declare_line" ] || \
  fail "the GPTK pre-init read must happen before the bundle declaration"
[ "$declare_line" -lt "$init_line" ] || \
  fail "the bundle declaration must happen before SDL_Init"
staged_line=$(grep -n 'nxinput_sdl_seam_stage_before_init' "$JOYPAD" | head -1 | cut -d: -f1)
[ -n "$staged_line" ] && [ "$preinit_line" -lt "$staged_line" ] || \
  fail "the GPTK pre-init read must happen before the live-mapping staging"

# --- 2. `0` benign, `< 0` fatal (mission cases 18/19) ---
grep -q 'nxc6_declare_port_bundle_for_layout(game_dir, nx_face_layout) < 0' \
  "$JOYPAD" || fail "the declare boundary must fail ONLY on < 0"
if grep -q 'nxc6_declare_port_bundle[a-z_]*(game_dir[^)]*) <= 0' "$JOYPAD"; then
  fail "the benign 0 return must never fail the joypad driver (<= 0 found)"
fi

# --- 3. One read: init_once consumes the preinit result, no second load ---
grep -q 'nxinput_gptk_preinit_load' "$GLUE" || \
  fail "the glue no longer uses the canonical pre-init loader"
if grep -q 'nxinput_gptk_load_at' "$GLUE"; then
  fail "a second GPTK read exists outside the pre-init boundary (TOCTOU)"
fi

# --- 4. The three bundles and the invariant base (mission 5.4) ---
MUTABLE_GUID=19000000010000000100000000010000
for bundle in controllers.nxb controllers-modern.nxb controllers-retro.nxb; do
  [ -f "$PORT/$bundle" ] && [ ! -L "$PORT/$bundle" ] || \
    fail "$bundle must be a regular, non-symlink file"
  head -1 "$PORT/$bundle" | grep -qx 'NXCONTROLLER_PROFILES/1' || \
    fail "$bundle lost the V1 header (no new bundle schema is ever invented)"
done
if grep -q "^$MUTABLE_GUID," "$PORT/controllers.nxb"; then
  fail "the INVARIANT base bundle froze the mutable modern/retro GUID again"
fi
for variant in modern retro; do
  count=$(grep -c "^$MUTABLE_GUID," "$PORT/controllers-$variant.nxb")
  [ "$count" = 1 ] || \
    fail "controllers-$variant.nxb must carry exactly one official line"
done
# The pair diverges ONLY in the authorized A/B/X/Y face bindings.
mod_line=$(grep "^$MUTABLE_GUID," "$PORT/controllers-modern.nxb")
ret_line=$(grep "^$MUTABLE_GUID," "$PORT/controllers-retro.nxb")
strip_face() { printf '%s' "$1" | tr ',' '\n' | grep -v '^[abxy]:' | sort; }
[ "$(strip_face "$mod_line")" = "$(strip_face "$ret_line")" ] || \
  fail "the modern/retro pair diverges outside the authorized A/B/X/Y face"
[ "$mod_line" != "$ret_line" ] || \
  fail "the modern/retro pair must actually differ in the face bindings"

# --- 5. nxproject pins the trio (schema 3, auto, complete pair) ---
python3 - "$PORT" <<'PY'
import hashlib, json, sys
port = sys.argv[1]
project = json.load(open(port + "/nxproject.json"))
controls = project["controls"]
assert controls["schema"] == 3, "controls.schema must be 3"
assert controls.get("face_layout", "auto") == "auto", \
    "the shipped default FACE_LAYOUT must be auto"
profiles = controls["controller_profiles"]
assert profiles["enabled"] is True
def sha(name):
    return hashlib.sha256(open(port + "/" + name, "rb").read()).hexdigest()
assert profiles["bundle"] == "controllers.nxb"
assert profiles["sha256"] == sha("controllers.nxb"), "base bundle pin drifted"
variants = profiles["face_layout_variants"]
assert set(variants) == {"modern", "retro"}, "the pair must be complete"
for name in ("modern", "retro"):
    entry = variants[name]
    assert entry["bundle"] == "controllers-%s.nxb" % name
    assert entry["sha256"] == sha(entry["bundle"]), \
        "variant %s pin drifted" % name
payload = {item["path"]: item["sha256"]
           for item in project["package_payload"]}
for name in ("controllers.nxb", "controllers-modern.nxb",
             "controllers-retro.nxb"):
    assert payload.get(name) == sha(name), \
        "package_payload pin drifted for %s" % name
print("nxproject trio pins: OK")
PY

# --- 6. Raw stays undeclared (mission case 37) ---
grep -q 'unset NXINPUT_RAW_CONSUMER_DECLARED' "$PORT/port-env.sh" || \
  fail "port-env.sh no longer clears the raw-consumer declaration"
if grep -rn 'NXINPUT_RAW_CONSUMER_DECLARED=1' "$PORT/src" "$PORT/port-env.sh" \
     2>/dev/null | grep -v '^\s*#'; then
  fail "something declares a raw consumer the engine cannot honour"
fi

# --- 7. The 98e051f generic fallback stays quarantined (cases 39/40) ---
for token in nx_add_generic_gamepad_mappings 'Generic Xbox Fallback'; do
  if grep -rn -F "$token" "$PORT/src" 2>/dev/null; then
    fail "quarantined generic-fallback token present: $token"
  fi
done
if grep -rnE 'a:b[0-9]+,b:b[0-9]+,x:b[0-9]+,y:b[0-9]+' "$PORT/src" \
     --include='*.c' --include='*.cpp' --include='*.h' 2>/dev/null | \
     grep -v 'src/nxinput/'; then
  fail "a synthesized mapping literal entered engine source"
fi
for token in muOS TrimUI ArkOS ROCKNIX Knulli; do
  if grep -rn "\"$token" "$PORT/src/godot_engine" 2>/dev/null; then
    fail "a CFW/model selector string entered engine source: $token"
  fi
done

# --- 8. The C6 receipt reaches the normal log (mission 5.9) ---
grep -q 'fprintf(stderr, "%s\\n", line)' "$PORT/src/nxinput/nxc6_glue.c" || \
  fail "the vendored glue lost the stderr receipt sink"

# --- 9. Visual-source allowlist (mission 11.1) -----------------------------
# Everything under src/ except the input seam must stay byte-identical to
# the safe base d4044004 (whose visual set is the physically approved 0.2.8
# facade). The allowlist is by DIRECTORY/FILE, never by "looks harmless".
SAFE_BASE=d4044004ed693073bf85d63a7921b5a44eda0117
REPO=$(git -C "$PORT" rev-parse --show-toplevel)
PREFIX=$(git -C "$PORT" rev-parse --show-prefix)
changed=$(git -C "$REPO" diff --name-only "$SAFE_BASE" -- "${PREFIX}src" || true)
# 0.2.16 (WAYLAND_GEOMETRY_PROOF): the SDL2 window geometry authority is a
# deliberate change to the video seam -- the fbdev display server pair, the
# extracted geometry unit, the frame-proof resize entry point, the engine patch
# that mirrors those files byte-for-byte and the neutral GL_VERSION facade
# string. They are allowed BY FILE; the fbdev/EGL provider path inside them is
# frozen by recipes/test_runtime_contract.py. Move SAFE_BASE to the 0.2.16
# commit once the new engine is physically approved.
# 0.2.17 (logical-player pad set): the input seam gains tearscape_padset.{cpp,h}
# (pure unit, gated by test_tearscape_padset.cpp) and the input policy header
# loses the retired "primary pad" helpers. Input files, never visual ones.
# 0.2.17 (GPTK evidence receipt): tearscape_gptk_receipt.{cpp,h} (pure unit,
# gated by test_tearscape_gptk_receipt.cpp) writes the NXGPTK_RECEIPT lines.
allow_re='^ports/tearscape/src/(nxinput/|godot_engine/v4-universal/drivers/sdl/(joypad_sdl\.cpp|nxinput_gptk_godot\.(cpp|h)|tearscape_padset\.(cpp|h)|tearscape_gptk_receipt\.(cpp|h)|tearscape_gptk_input_policy\.h|SCsub)$|godot_engine/v4-universal/platform/linuxbsd/fbdev/(display_server_fbdev\.(cpp|h)|nx_sdl2_geometry\.(cpp|h)|nxgl_frame_proof_adapter\.[ch]|nxgl_godot_frame_proof\.h)$|godot_engine/godot-4\.6\.1-nextos\.patch$|shim/nx_gles3_core\.c$)'
bad=$(printf '%s\n' "$changed" | grep -vE "$allow_re" | grep -v '^$' || true)
if [ -n "$bad" ]; then
  printf '%s\n' "$bad" >&2
  fail "engine source changed outside the input allowlist (visual facade is frozen)"
fi

printf 'tearscape-layout-authority: ALL PASS\n'
