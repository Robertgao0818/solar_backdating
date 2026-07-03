# dinov3_chips test fixture

Three real GEHI historical review chips used by
`tests/temporal/test_dinov3_scorer.py` for the DINOv3 scorer backbone-parity
and determinism tests (shape / ordering / reproducibility only — **never
accuracy**).

## Provenance

- Source imagery: GEHistoricalImagery (GEHI) z=19 historical satellite chips
  (≈ 0.25 m), rendered as 204×204 RGB review PNGs during the ISSUE-02 panel
  repair run.
- Source directory (on `~/zasolar_data`, not committed):

  ```
  /home/gaosh/zasolar_data/geid_temporal/panel_repair_20260703/chips_frozen/\
  jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_c0008678/z19/
  ```

- Anchor: `jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_c0008678`
  (Johannesburg full-382 Vexcel census, unified_A per-detection, target T01).

## Files (renamed to short, stable, date-based names)

| fixture file        | source file                                                                                            |
|---------------------|--------------------------------------------------------------------------------------------------------|
| `chip_20090312.png` | `..._c0008678_20090312_v125.target-T01-037d5e4137.png`                                                 |
| `chip_20090627.png` | `..._c0008678_20090627_v125.target-T01-037d5e4137.png`                                                 |
| `chip_20090726.png` | `..._c0008678_20090726_v125.target-T01-037d5e4137.png`                                                 |

Each is a 204×204 RGB PNG, ≈ 38–40 KB (total fixture < 128 KB).
