#!/bin/bash
# Directed host gate for the NEXTOSCONTROLLERS live runtime (no device, no ZIP).
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")" && pwd -P)
SRC="$HERE/../../src/nxinput"
OUT=$(mktemp -d /tmp/claude-1000/nxgptk-gate-XXXX 2>/dev/null || mktemp -d)
trap 'rm -rf "$OUT"' EXIT
if (( $# > 1 )); then
	printf 'usage: %s [generated-NEXTOSCONTROLLERS.gptk]\n' "$0" >&2
	exit 2
fi
cc -O1 -Wall -Wextra -I"$SRC" -o "$OUT/gate" \
  "$HERE/test_gptk_runtime_host.c" \
  "$SRC/nxinput_gptk.c" "$SRC/nxinput_gptk_live.c" \
  "$SRC/nxinput_gptk_loader.c" "$SRC/nxinput_gptk_motion.c" -lm
if (( $# == 1 )); then
	"$OUT/gate" "$1"
else
	"$OUT/gate"
fi
cc -O1 -Wall -Wextra \
	-I"$SRC" \
	-I"$HERE/../../src/godot_engine/v4-universal/drivers/sdl" \
  -o "$OUT/context-gate" "$HERE/test_tearscape_gptk_context.c"
"$OUT/context-gate"
c++ -std=c++17 -Wall -Wextra -Werror \
	-I"$HERE/../../src/godot_engine/v4-universal/drivers/sdl" \
	-c "$HERE/../../src/godot_engine/v4-universal/drivers/sdl/nxinput_gptk_godot.cpp" \
	-o "$OUT/nxinput-gptk-nonfbdev.o"
printf 'TEARSCAPE GPTK NON-FBDEV TU: PASS\n'
# 0.2.17: the logical-player pad set (union, max-magnitude axes, same-pad
# chord, cross-pad denial logged once, compaction, cap) is a pure unit with
# no Godot/SDL header; prove it on the host.
c++ -std=c++17 -Wall -Wextra -Werror \
	-I"$HERE/../../src/godot_engine/v4-universal/drivers/sdl" \
	-o "$OUT/padset-gate" "$HERE/test_tearscape_padset.cpp" \
	"$HERE/../../src/godot_engine/v4-universal/drivers/sdl/tearscape_padset.cpp"
"$OUT/padset-gate"
# 0.2.17: the GPTK evidence receipt (JSON lines read back by the framework's
# automated on-device proof and the release lock) is a pure unit too: exact
# line shapes, adapter sink-id table, append/line-buffered file, release
# attribution and vector EDGE semantics are proven on the host.
c++ -std=c++17 -Wall -Wextra -Werror \
	-I"$HERE/../../src/godot_engine/v4-universal/drivers/sdl" \
	-o "$OUT/receipt-gate" "$HERE/test_tearscape_gptk_receipt.cpp" \
	"$HERE/../../src/godot_engine/v4-universal/drivers/sdl/tearscape_gptk_receipt.cpp"
TMPDIR="$OUT" "$OUT/receipt-gate"

bash "$HERE/test_layout_authority.sh"
