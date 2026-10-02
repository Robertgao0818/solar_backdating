# DATA — JHB RUN 3 presence sequences attached (2026-09-23)

Parent: [`RUN-fullscan-gemini-backdating-run3-2026-07-18.md`](RUN-fullscan-gemini-backdating-run3-2026-07-18.md).

The econ handover under `deliverable_run3_2026-07-18/` still has only the
collapsed bracket. Raw 0/1 sequences for that same 41,393-anchor RUN 3
scan now sit beside it, without modifying the frozen csv/gpkg:

`~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/deliverable_run3_sequences_2026-09-23/`

Script: `scripts/temporal/export_jhb_run3_presence_sequences.py`.
Gate: replay of `infer_install_dates.py` with the 2026-08-13 census-date
fence turned off matches `run3_v2/intervals/install_intervals_all.csv` on
all 41,393 anchors (status, bracket, counts, confidence). 311,195 frames.
6,273 dip-repair flips on 3,263 anchors. Column grammar and the
post-census caveat are in that directory's `README.md`.

June 2026 and RUN 2 scan states are not on disk; this extract does not
cover them.

## Date x installed panel

Dropbox handover (separate from the frozen install-date packages):
`~/Dropbox/RA_Solar/Gao/backdating/presence_panels/`. Johannesburg file
`johannesburg/jhb_run3_install_panel.csv`, sha256
`b762530ea946330b8849e3776de9c988e69f421473096f55ecaa611741fff1b2`.
Cape Town is reserved as `cape_town/` and is not in the folder yet.
Raw RUN 3 scan states: `johannesburg/scan_states/jhb_run3_scan_states.tar`
(41,393 JSON under `a24/scan_states` and `a48/scan_states`, sha256
`a6d32c4515bd1a4b0947330c40ceb20e26c794ee0e726d2e6724c5a971768375`).

`jhb_run3_install_panel.csv` in the same directory (script
`scripts/temporal/build_jhb_run3_install_panel.py`) is one row per
`(source_feature_id, capture_date)` on the RUN 3 vintage catalog:
1,174,706 rows. `installed` is 0/1 where a usable Gemini frame exists
(299,576, after dip repair) or where the published bracket determines
the date (598,538 implied absent before the last-absent endpoint,
213,091 implied present on or after the present-side endpoint, including
a census flight date that Gemini did not score). 2,314 catalog dates
inside a published bracket and 61,186 dates on the 2,888 undated anchors
stay blank. Those blanks are the residual adaptive-scan hole; they are
not filled.
