#!/usr/bin/env bash
# Optional official Linux binary: includes CPU OpenImageDenoise omitted by some distros.
# Usage: bash scripts/install_local_blender.sh /opt/greenplan
set -euo pipefail
DEST="${1:?installation directory required}"
VERSION=4.5.3
ARCHIVE="blender-${VERSION}-linux-x64.tar.xz"
BASE="https://download.blender.org/release/Blender4.5"
mkdir -p "$DEST"
if [ ! -f "$DEST/blender-${VERSION}-linux-x64/.greenplan-complete" ]; then
    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT
    curl -fsSL "$BASE/$ARCHIVE" -o "$WORK/$ARCHIVE"
    curl -fsSL "$BASE/blender-${VERSION}.sha256" -o "$WORK/checksums"
    (cd "$WORK" && awk -v file="$ARCHIVE" '$2==file {print}' checksums > selected.sha256 && test -s selected.sha256 && sha256sum -c selected.sha256)
    tar xJf "$WORK/$ARCHIVE" -C "$DEST"
    touch "$DEST/blender-${VERSION}-linux-x64/.greenplan-complete"
fi
printf 'Set BLENDER_EXECUTABLE=%s/blender-%s-linux-x64/blender in the API environment.\n' "$DEST" "$VERSION"
