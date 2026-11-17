#!/bin/bash
# mlc-research: isolated TempestExtremes install on the ewb LXC.
# Everything lives under /opt/ewb-live/opt (micromamba root) — nothing outside
# /opt/ewb-live is touched and the project .venv is untouched. The box has no
# compiler and no bzip2, hence the python-bz2 unpack of the micromamba tarball.
set -euo pipefail
ROOT=/opt/ewb-live/opt
MM=$ROOT/mm
mkdir -p "$ROOT"
df -h /opt | tail -1
if [ ! -x "$ROOT/micromamba" ]; then
  PY=/opt/ewb-live/.venv/bin/python
  cd "$ROOT"
  curl -Ls -o mm.tar.bz2 https://micro.mamba.pm/api/micromamba/linux-64/latest
  "$PY" -c "import bz2;open('mm.tar','wb').write(bz2.decompress(open('mm.tar.bz2','rb').read()))"
  rm -f mm.tar.bz2
  tar -xf mm.tar bin/micromamba
  mv bin/micromamba "$ROOT/micromamba"; rm -rf bin mm.tar
  chmod +x "$ROOT/micromamba"
fi
export MAMBA_ROOT_PREFIX="$MM"
export MAMBA_EXE="$ROOT/micromamba"
if [ -x "$MM/envs/te/bin/DetectNodes" ]; then
  echo "te env already present"
else
  "$ROOT/micromamba" create -y -q -n te -c conda-forge --override-channels tempest-extremes
fi
echo "== versions =="
"$MM/envs/te/bin/DetectNodes" --version 2>&1 | head -3 || true
"$MM/envs/te/bin/StitchNodes" --version 2>&1 | head -3 || true
ls "$MM/envs/te/bin" | egrep -i 'Detect|Stitch|Calc|Blob|Density|Project|Split|Merge' | tr '\n' ' '; echo
du -sh "$MM" 2>/dev/null
df -h /opt | tail -1
