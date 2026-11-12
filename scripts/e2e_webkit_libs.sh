#!/usr/bin/env bash
# Make Playwright WebKit runnable WITHOUT root (dev containers lacking the GTK/
# GStreamer system libraries). With root, `playwright install-deps webkit` is
# simpler — use that instead.
#
# Downloads the missing Debian runtime libraries (apt-get download, no install)
# into .e2e-webkit-libs/ and patches Playwright's WebKit wrapper to append that
# directory to LD_LIBRARY_PATH via $EWB_EXTRA_LD. scripts/e2e.sh exports
# EWB_EXTRA_LD + PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS automatically when
# the directory exists.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HERE/.e2e-webkit-libs"
PW="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
mkdir -p "$DEST/debs" "$DEST/root"
cd "$DEST/debs"
PKGS=$(apt-get -s install --no-install-recommends libgtk-4-1 libgstreamer-plugins-base1.0-0 \
  libxslt1.1 libevent-2.1-7t64 libopus0 libflite1 libwebpdemux2 libgstreamer-gl1.0-0 \
  libgstreamer-plugins-bad1.0-0 libgraphene-1.0-0 libenchant-2-2 libmanette-0.2-0 libhyphen0 \
  libjxl0.11 libavif16 libharfbuzz-icu0 libwebpmux3 libsoup-3.0-0 libbacktrace0 2>/dev/null \
  | awk '/^Inst/{print $2}' | grep -v -E '^libwpewebkit')
# shellcheck disable=SC2086
apt-get download $PKGS
for d in *.deb; do dpkg -x "$d" "$DEST/root"; done
# the bundle ships its own libWPEWebKit; a distro copy would shadow it
rm -rf "$DEST"/root/usr/lib/x86_64-linux-gnu/libWPEWebKit* "$DEST"/root/usr/lib/x86_64-linux-gnu/wpe-webkit-2.0
for w in "$PW"/webkit-*/minibrowser-wpe/MiniBrowser; do
  [ -f "$w" ] || continue
  sed -i 's|^export LD_LIBRARY_PATH=.*|export LD_LIBRARY_PATH="${MYDIR}/lib:${MYDIR}/sys/lib:${EWB_EXTRA_LD}"|' "$w"
  echo "patched $w"
done
echo "done: libs in $DEST/root (export EWB_EXTRA_LD=$DEST/root/usr/lib/x86_64-linux-gnu)"
