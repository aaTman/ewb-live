"""Skip the gcsfs ExtendedGcsFileSystem storage-control probe.

Why: ``gcsfs.extended_gcsfs.ExtendedGcsFileSystem._get_bucket_type`` calls
the Cloud Storage Control ``get_storage_layout`` API with ADC credentials
before zarr metadata reads. With no ADC on this host every call costs
~12 s (GCE metadata discovery timeout) and then logs
"Could not determine bucket type ... falling back to GCSFileSystem" — and
UNKNOWN results are deliberately *not* cached, so it repeats per request.

Our GCS sources are two well-known public, flat, non-hierarchical buckets:
  - gs://gcp-public-data-arco-era5
  - gs://extremeweatherbench
For those we answer NON_HIERARCHICAL instantly (identical to the fallback
path). For any other bucket the original method runs unchanged.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

FLAT_PUBLIC_BUCKETS = frozenset({"gcp-public-data-arco-era5", "extremeweatherbench"})

_applied = False


def apply() -> bool:
    """Install the patch; idempotent. Returns True if active."""
    global _applied
    if _applied:
        return True
    os.environ.setdefault("NO_GCE_CHECK", "True")
    try:
        from gcsfs import extended_gcsfs as ext
    except Exception:  # gcsfs not installed / different layout
        return False
    orig = getattr(ext.ExtendedGcsFileSystem, "_get_bucket_type", None)
    if orig is None:
        return False

    async def _get_bucket_type(self, bucket):  # noqa: ANN001
        if bucket in FLAT_PUBLIC_BUCKETS:
            return ext.BucketType.NON_HIERARCHICAL
        return await orig(self, bucket)

    ext.ExtendedGcsFileSystem._get_bucket_type = _get_bucket_type
    _applied = True
    logger.debug("gcsfs storage-control probe patched for %s", sorted(FLAT_PUBLIC_BUCKETS))
    return True
