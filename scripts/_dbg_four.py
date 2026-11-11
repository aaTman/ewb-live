#!/usr/bin/env python
"""One-off debug: which FOUR_v100_GFS variables are loaded vs virtual after
open_virtual_dataset with the builder's LOADABLE list (runs on ewb box)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import virtualizarr as vz
from virtualizarr.parsers import HDFParser

from ewb_live.stores import mlwp_icechunk as M

url = ("s3://noaa-oar-mlwp-data/FOUR_v100_GFS/2021/0615/"
       "FOUR_v100_GFS_2021061500_f000_f240_06.nc")
vds = vz.open_virtual_dataset(url, registry=M._registry(), parser=HDFParser(),
                              loadable_variables=M.LOADABLE, decode_times=True)
for name, da in vds.variables.items():
    kind = type(da.data).__name__
    print(f"{name:10s} {kind:16s} dims={da.dims} shape={da.shape} dtype={da.dtype}")
print("coords:", list(vds.coords))
print("loadable requested:", M.LOADABLE)
