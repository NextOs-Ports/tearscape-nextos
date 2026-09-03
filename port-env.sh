#!/bin/bash
# Game-specific environment only. Host discovery, splash, locking and
# lifecycle supervision remain owned by the generated nxbootstrap launcher.

# ROCKNIX/sway (Wayland) fullscreens the port by matching the window app_id to
# the launched executable name (its helper searches app_id=tearscape-nextos).
# The launcher's generic default advertises the port id (tearscape), which never
# matches, so on a portrait 1080x1920 panel (Retroid Pocket 5) the window was
# never fullscreened and the game rendered in a corner. Advertise the exact
# executable name so the SDL2 Wayland surface matches the helper. Harmless on
# fbdev/KMSDRM firmwares (muOS/dArkOS/EmuELEC), which never read these.
export SDL_APP_ID="tearscape-nextos"
export SDL_VIDEO_WAYLAND_WMCLASS="tearscape-nextos"

for NX_TEARSCAPE_SAVE_DIR in "$GAMEDIR/.local" "$GAMEDIR/.local/share"; do
  if [ -L "$NX_TEARSCAPE_SAVE_DIR" ]; then
    echo "ERROR: Tearscape save directory is unsafe"
    exit 1
  fi
done
mkdir -p -- "$GAMEDIR/.local/share" || {
  echo "ERROR: cannot create the Tearscape save directory"
  exit 1
}
XDG_DATA_HOME="$GAMEDIR/.local/share"
export XDG_DATA_HOME

for NX_TEARSCAPE_SHIM in "$GAMEDIR/lib/libEGL.so" "$GAMEDIR/lib/libGLESv2.so"; do
  if [ ! -f "$NX_TEARSCAPE_SHIM" ] || [ -L "$NX_TEARSCAPE_SHIM" ]; then
    echo "ERROR: Tearscape graphics shim is missing or unsafe"
    exit 1
  fi
done
unset NX_TEARSCAPE_SHIM NX_TEARSCAPE_SAVE_DIR

# The optional CRT pass is intentionally a no-op on every device. Its
# SCREEN_TEXTURE pyramid is both very expensive on Mali-450 and has produced
# terminal black-screen-with-audio failures. Keep the owner's menu toggle and
# save format intact, but atomically replace only that extracted shader before
# Godot starts: OFF and ON therefore use the same native, effect-free image.
NX_TEARSCAPE_CRT_DIR="$GAMEDIR/game/addons/crt"
NX_TEARSCAPE_CRT_SHADER="$NX_TEARSCAPE_CRT_DIR/crt.gdshader"
NX_TEARSCAPE_CRT_TEMP="$NX_TEARSCAPE_CRT_DIR/.crt-disabled.$$"
if [ ! -d "$NX_TEARSCAPE_CRT_DIR" ] || [ -L "$NX_TEARSCAPE_CRT_DIR" ] || \
   [ ! -f "$NX_TEARSCAPE_CRT_SHADER" ] || [ -L "$NX_TEARSCAPE_CRT_SHADER" ]; then
  echo "ERROR: Tearscape CRT shader path is missing or unsafe"
  exit 1
fi
if [ -e "$NX_TEARSCAPE_CRT_TEMP" ] || [ -L "$NX_TEARSCAPE_CRT_TEMP" ]; then
  echo "ERROR: Tearscape CRT shader temporary path is unsafe"
  exit 1
fi
if ! (
  umask 022
  set -o noclobber
  printf '%s\n' \
    '// Tearscape/NextOS: CRT intentionally disabled.' \
    'shader_type canvas_item;' \
    '' \
    'void fragment() {' \
    '    discard;' \
    '}' > "$NX_TEARSCAPE_CRT_TEMP"
); then
  echo "ERROR: cannot prepare the disabled Tearscape CRT shader"
  exit 1
fi
if ! mv -f -- "$NX_TEARSCAPE_CRT_TEMP" "$NX_TEARSCAPE_CRT_SHADER"; then
  rm -f -- "$NX_TEARSCAPE_CRT_TEMP"
  echo "ERROR: cannot disable the Tearscape CRT shader"
  exit 1
fi
unset NX_TEARSCAPE_CRT_DIR NX_TEARSCAPE_CRT_SHADER NX_TEARSCAPE_CRT_TEMP

# These opt-ins are consumed only by this port's custom Godot executable.
NX_TEARSCAPE_DEFAULTS=1
NX_TEARSCAPE_EGL_LIBRARY="$GAMEDIR/lib/libEGL.so"
NX_TEARSCAPE_GLES2_LIBRARY="$GAMEDIR/lib/libGLESv2.so"
NX_TEARSCAPE_NATIVE_GLES3=0
NX_TEARSCAPE_SCREEN_MIP_EMULATION=0
NX_BATCH_INSTANCING=1
GODOT_SILENCE_ROOT_WARNING=1

# The universal floor is a physical GLES2 context. On every family the
# packaged translation shims stay the EGL/GLES pair and the raw provider owns
# presentation. When a usable DRM node exists (KMSDRM firmware), the shim
# additionally drives the firmware SDL2 as the window/context/page-flip owner
# (the physically proven Regenesis facade); the firmware pair is handed to
# SDL as one atomic pair only. A native-GLES3 main path is forbidden: it
# proved black-screen-with-audio on DRM Mali devices while every indirect
# signal (context, page flip, clean shader log) looked healthy.
NX_TEARSCAPE_DRM_OK=0
for NX_TEARSCAPE_DRM_NODE in \
  /dev/dri/card0 /dev/dri/card1 /dev/dri/card2 /dev/dri/card3 \
  /dev/dri/card4 /dev/dri/card5 /dev/dri/card6 /dev/dri/card7; do
  if [ -c "$NX_TEARSCAPE_DRM_NODE" ] && [ -r "$NX_TEARSCAPE_DRM_NODE" ] && [ -w "$NX_TEARSCAPE_DRM_NODE" ]; then
    NX_TEARSCAPE_DRM_OK=1
    break
  fi
done
if [ "$NX_TEARSCAPE_DRM_OK" = 1 ]; then
  NX_TEARSCAPE_SDL_EGL=1
  export NX_TEARSCAPE_SDL_EGL
  if [ -z "${SDL_VIDEO_EGL_DRIVER:-}" ] || [ -z "${SDL_VIDEO_GL_DRIVER:-}" ]; then
    for NX_TEARSCAPE_GL_DIR in /usr/lib/aarch64-linux-gnu /usr/lib64 /usr/lib; do
      if [ -e "$NX_TEARSCAPE_GL_DIR/libEGL.so" ] && [ -e "$NX_TEARSCAPE_GL_DIR/libGLESv2.so" ]; then
        SDL_VIDEO_EGL_DRIVER="$NX_TEARSCAPE_GL_DIR/libEGL.so"
        SDL_VIDEO_GL_DRIVER="$NX_TEARSCAPE_GL_DIR/libGLESv2.so"
        export SDL_VIDEO_EGL_DRIVER SDL_VIDEO_GL_DRIVER
        break
      fi
    done
  fi
fi
unset NX_TEARSCAPE_DRM_NODE NX_TEARSCAPE_DRM_OK NX_TEARSCAPE_GL_DIR
# Video provider by capability. On fbdev/KMSDRM firmwares (muOS/dArkOS/EmuELEC)
# the raw fbdev provider owns the framebuffer and fills the screen. On a Wayland
# session (ROCKNIX/sway) fbdev writes to the raw framebuffer and is NEVER a
# compositor window, so sway cannot fullscreen it: on the Retroid Pocket 5 the
# sway helper searched for a window (app_id=tearscape-nextos) that did not exist
# ("No matching node" in a loop) and the game rendered in a corner of the
# portrait 1080x1920 panel. The engine's SDL2 provider instead opens a real
# Wayland surface (named by SDL_VIDEO_WAYLAND_WMCLASS above), which sway
# composits and fullscreens. So: on a live Wayland session pick sdl2, otherwise
# keep the fbdev raw provider. An explicit inherited value always wins.
if [ -z "${NX_TEARSCAPE_VIDEO:-}" ]; then
  if [ -n "${WAYLAND_DISPLAY:-}" ] || \
     ls "${XDG_RUNTIME_DIR:-/run}"/wayland-* >/dev/null 2>&1; then
    NX_TEARSCAPE_VIDEO=sdl2
  else
    NX_TEARSCAPE_VIDEO=fbdev
  fi
fi

# The V4 C6 seam consumes PortMaster's mapping before SDL3 announces the real
# pad. It measures that exact device, resolves the sovereign authority order
# and fails closed on malformed or unreachable mappings. Real CFW databases
# may repeat a GUID: nxinput 0.10.0 follows SDL's last-wins semantics and
# records that resolution in the receipt. The joydev/evdev domain of each
# candidate line is decided by semantic proof against the measured key set;
# an ambiguous source yields instead of passing silently, and names/models
# never authorize conversion. Every admission receipt is written to BOTH the
# durable file below and the process stderr, so the port's normal log (and
# the support bundle) carries the same sanitized evidence.
# Keep SDL_GAMECONTROLLERCONFIG untouched here: the engine stages and removes
# it atomically before SDL_Init, so SDL cannot import it at a higher priority.
NXC6_SEAM=1
NXC6_RECEIPT="$GAMEDIR/.local/nxinput-c6.log"
if [ -L "$NXC6_RECEIPT" ]; then
  echo "ERROR: Tearscape controller receipt is unsafe"
  exit 1
fi
unset NXINPUT_RAW_CONSUMER_DECLARED

# WAYLAND_GEOMETRY_PROOF (0.2.16): the display server appends one JSON line
# per geometry event (nx-geometry-proof/1) -- the provider, SDL video driver,
# app id, raw framebuffer vs SDL display bounds, the configured/drawable size
# after the first authoritative configure and every later resize. The gate
# (recipes/make_geometry_proof.py) fails a portrait/cropped window, an empty
# app id or a timed-out configure. Truncated per launch so a receipt always
# describes this run only.
NXGEOMETRY_RECEIPT="$GAMEDIR/nxgeometry-receipt.jsonl"
if [ -L "$NXGEOMETRY_RECEIPT" ]; then
  echo "ERROR: Tearscape geometry receipt is unsafe"
  exit 1
fi
: > "$NXGEOMETRY_RECEIPT" || {
  echo "ERROR: cannot prepare the Tearscape geometry receipt"
  exit 1
}

# GPTK evidence receipt (0.2.17): the live controls runtime appends one JSON
# line per proven event (nxinput-gptk-event-evidence/1): the runtime seal,
# every proven context, every delivered press/release and vector gesture edge
# (start and return to neutral, never per frame) with the ADAPTER sink id, and
# every suppressed (null) press. The framework's automated on-device controls
# proof (nx-device-input-proof) and the release lock (nx-input-proof-lock)
# read this file back. Truncated per launch so it describes this run only.
NXGPTK_RECEIPT="$GAMEDIR/nxgptk-receipt.jsonl"
if [ -L "$NXGPTK_RECEIPT" ]; then
  echo "ERROR: Tearscape controls receipt is unsafe"
  exit 1
fi
: > "$NXGPTK_RECEIPT" || {
  echo "ERROR: cannot prepare the Tearscape controls receipt"
  exit 1
}

export NX_TEARSCAPE_DEFAULTS NX_TEARSCAPE_EGL_LIBRARY NX_TEARSCAPE_GLES2_LIBRARY
export NX_TEARSCAPE_NATIVE_GLES3 NX_TEARSCAPE_SCREEN_MIP_EMULATION
export NX_TEARSCAPE_VIDEO NX_BATCH_INSTANCING GODOT_SILENCE_ROOT_WARNING
export NXC6_SEAM NXC6_RECEIPT NXGEOMETRY_RECEIPT NXGPTK_RECEIPT

# The Godot project lives in game/. Entering it lets the engine find
# project.binary through its normal CWD project discovery, with no --path.
cd "$GAMEDIR/game" || {
  echo "ERROR: cannot enter the Tearscape runtime directory"
  exit 1
}
