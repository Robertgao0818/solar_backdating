"""Package-level test setup for ``tests/temporal``.

Import-order guard (2026-07-19). In this shared ZAsolar venv, importing
``fiona`` (GDAL) *before* ``pyarrow`` makes ``pyarrow.lib`` fail to load:

    ImportError: .../pyarrow/libarrow_substrait.so.2300: undefined symbol:
    _ZN4absl...18container_internal24GetHashRefForEmptyHasher...

fiona's bundled Abseil / libstdc++ shadows the symbols pyarrow's C extension
resolves at import, so whichever of the two loads first wins the process. Several
tests here import fiona/geopandas at module top level
(``test_build_install_dated_deliverable``, ``test_build_inventory_chip_groups``);
when one is collected before a pandas ``to_parquet``/``read_parquet`` test
(``test_run3_native_manifest``, ``test_r1_marker_free_crops``), pandas then
reports "Unable to find a usable engine; tried using: 'pyarrow', 'fastparquet'".

``conftest.py`` is imported before any test module in this package, so importing
pyarrow HERE pins its libs first and the conflict cannot arise regardless of
collection order or which fiona-importing module runs. This is the fix on the
polluting mechanism (GDAL/pyarrow load order), not a per-victim workaround.

Do not remove this import, and keep it ahead of any fiona/geopandas import.
"""

import pyarrow  # noqa: F401  -- must load before any fiona/GDAL import; see above
