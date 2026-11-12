"""EWB Live — live extreme-weather detection and scoring pipeline.

Importing this package installs one environment speedup:
``ewb_live._gcsfs_speedup.apply()`` disables the storage-control "bucket
type" probe that gcsfs >= 2026.10 runs for every GCS metadata operation.
Our buckets (gcp-public-data-arco-era5, extremeweatherbench) are flat,
public, non-HNS buckets, and the probe uses ADC credentials this machine
does not have, so each attempt burns ~12 s in metadata-server discovery
before falling back to the plain GCSFileSystem path anyway. The patch
returns the correct answer (NON_HIERARCHICAL) for our buckets immediately
and preserves upstream behaviour for anything else.
"""

from . import _gcsfs_speedup as _gcs  # noqa: F401

_gcs.apply()

__all__ = ["_gcs"]
