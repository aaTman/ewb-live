#!/bin/bash
# mlc-research environment probe (read-only; runs on the ewb LXC)
hostname; nproc; free -g | sed -n 2p
df -h /opt | tail -1
echo "== tools =="
for c in cmake g++ gcc make mpicxx conda mamba micromamba ncdump python3; do
  printf "%-12s %s\n" "$c" "$(command -v $c || echo MISSING)"
done
echo "== venv =="
. /opt/ewb-live/.venv/bin/activate
python -V
python - <<'PY'
import importlib
for m in ["xarray","dask","numpy","netCDF4","h5netcdf","zarr","gcsfs","pandas","scipy","requests"]:
    try:
        mod = importlib.import_module(m); print(m, getattr(mod, "__version__", "?"))
    except Exception as e:
        print(m, "MISSING")
PY
echo "== egress =="
for u in https://conda.anaconda.org/conda-forge/noarch/repodata.json https://storage.googleapis.com/storage/v1/b/gcp-public-data-arco-era5 https://github.com; do
  printf "%s -> %s\n" "$u" "$(curl -s -o /dev/null -m 10 -w '%{http_code}' $u)"
done
echo "== ewb-live dirs =="
ls /opt/ewb-live
echo "-- cache:"; ls /opt/ewb-live/data/cache 2>/dev/null
du -sh /opt/ewb-live/data/cache/truth 2>/dev/null
echo "== existing mirror =="
/opt/ewb-live/.venv/bin/python - <<'PY'
import json, pathlib
p = pathlib.Path("/opt/ewb-live/data/cache/truth/era5_mirror_manifest.json")
if p.exists():
    man = json.loads(p.read_text()); comp = man.get("complete", {})
    ks = sorted(comp)
    print("mirror hours:", len(ks), "first:", ks[0] if ks else None, "last:", ks[-1] if ks else None)
    import zarr
    st = zarr.open("/opt/ewb-live/data/cache/truth/era5.zarr", mode="r")
    print("arrays:", list(st.array_keys()))
    for k in st.array_keys():
        print(" ", k, st[k].shape, st[k].dtype, st[k].attrs.get("_ARRAY_DIMS") if hasattr(st[k],"attrs") else "")
else:
    print("no manifest")
PY
