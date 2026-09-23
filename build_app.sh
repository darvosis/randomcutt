#!/bin/bash
# build_app.sh - compila randomcutt a una app de macOS (dist/randomcutt.app).
#
# Uso:
#   ./build_app.sh               app para la arquitectura de este Mac (arm64 en M1/M2)
#   ./build_app.sh --universal   una sola app para Apple Silicon e Intel
#   ./build_app.sh --clean       borra build, dist y el venv antes de compilar
#
# Crea un venv aislado en .venv-build (no toca tu Python base), instala
# PyInstaller ahí y deja la app en dist/randomcutt.app, más un .zip para el
# release. ffmpeg NO se empaqueta: la app lo busca al iniciar.
#
# Necesita el Python de python.org (trae Tk 8.6 y es universal2). Para usar
# otro: PYTHON=/ruta/a/python3 ./build_app.sh
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
UNIVERSAL=0
for arg in "$@"; do
    case "$arg" in
        --universal) UNIVERSAL=1 ;;
        --clean)
            echo "limpiando build, dist y .venv-build"
            rm -rf build dist .venv-build ./*.spec
            ;;
        *) echo "opción desconocida: $arg" >&2; exit 1 ;;
    esac
done

# --- venv de build -----------------------------------------------------------
VPY=.venv-build/bin/python
if [ ! -x "$VPY" ]; then
    echo "creando venv de build con $("$PYTHON" -c 'import sys; print(sys.executable)') ..."
    "$PYTHON" -m venv .venv-build
fi
"$VPY" -m pip install --upgrade pip pyinstaller --quiet
if ! "$VPY" -c "import sys, tkinter; sys.exit(tkinter.TkVersion < 8.6)" 2>/dev/null; then
    echo "ese Python no trae Tk 8.6 (el de /usr/bin/python3 trae uno viejo)." >&2
    echo "Instala el de python.org o indica otro: PYTHON=/ruta/a/python3 ./build_app.sh" >&2
    exit 1
fi

# La caché de PyInstaller no siempre detecta cambios: empezar siempre de cero.
rm -rf build
mkdir -p build

# --- ícono: .icns a partir de los PNG que trae randomcutt.ico -----------------
ICONSET=build/randomcutt.iconset
mkdir -p "$ICONSET"
"$VPY" - "$ICONSET" <<'EOF'
import struct, sys
data = open("randomcutt.ico", "rb").read()
pngs = {}
for i in range(struct.unpack_from("<H", data, 4)[0]):
    w, _, _, _, _, _, size, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
    pngs[w or 256] = data[off:off + size]
names = {16: ["16x16"], 32: ["16x16@2x", "32x32"], 64: ["32x32@2x"],
         128: ["128x128"], 256: ["128x128@2x", "256x256"]}
for px, ns in names.items():
    for n in ns:
        with open(f"{sys.argv[1]}/icon_{n}.png", "wb") as f:
            f.write(pngs[px])
EOF
# El .ico llega hasta 256 px; el Finder usa 512 en los íconos grandes.
sips -z 512 512 "$ICONSET/icon_256x256.png" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
cp "$ICONSET/icon_256x256@2x.png" "$ICONSET/icon_512x512.png"
iconutil -c icns "$ICONSET" -o build/randomcutt.icns

# --- build -------------------------------------------------------------------
ARGS=(--noconfirm --windowed
      --name randomcutt
      --icon build/randomcutt.icns
      --exclude-module numpy
      --exclude-module PIL
      --exclude-module pytest
      --exclude-module setuptools)
ARCH=$(uname -m)
if [ "$UNIVERSAL" = 1 ]; then
    ARGS+=(--target-arch universal2)
    ARCH=universal
fi
echo "compilando ($ARCH) ..."
"$VPY" -m PyInstaller "${ARGS[@]}" randomcutt_v007.py

# --- zip para el release -----------------------------------------------------
# ditto y no zip: zip rompe los permisos y los symlinks del bundle.
ZIP="dist/randomcutt-macos-$ARCH.zip"
rm -f "$ZIP"
ditto -c -k --keepParent dist/randomcutt.app "$ZIP"

echo ""
echo "OK -> dist/randomcutt.app ($(du -sh dist/randomcutt.app | cut -f1))"
echo "      $ZIP ($(du -sh "$ZIP" | cut -f1))"
