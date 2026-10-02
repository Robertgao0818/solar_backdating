"""Audit Cape Town aerial DINO threshold savings against every reference polygon.

Read-only source inputs; full coverage including <30 m² objects. Writes diagnostic
CSV/JSON outputs only. Detector references are not human ground truth.
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import geopandas as gpd
import pyogrio, shapely
from shapely.geometry import box
ROOT=Path('/home/gao/zasolar_data/gate_pilot_2026-09/learning_curve_2026-09-28')
OUT=Path('/home/gao/zasolar_data/geid_temporal/aerial_gate_tail_review_20261002')
scores=pd.read_csv(OUT/'paired_scores.csv')
records=[]
for grid,group in scores.groupby('grid_id'):
    m=pd.read_csv(ROOT/'chips'/grid/'manifest.csv').set_index('chip_index')
    hits=pd.read_csv(ROOT/'chips'/grid/'intersections.csv')
    hits=hits[hits.layer=='post']
    polys=pyogrio.read_dataframe(ROOT/'vectors'/grid/'predictions_metric_cls_filtered.gpkg',fid_as_index=True)
    ids=sorted(hits.chip_index.unique())
    wins=gpd.GeoSeries([shapely.segmentize(box(*m.loc[i,['minx','miny','maxx','maxy']]),.000008) for i in ids],index=ids,crs=4326).to_crs(polys.crs)
    sc=group.set_index('chip_index').vit30
    for pid,h in hits.groupby('poly_id'):
        poly=shapely.make_valid(polys.loc[pid].geometry)
        indices=sorted(set(h.chip_index),key=lambda i:sc.loc[i],reverse=True)
        full_union=shapely.union_all(wins.loc[indices].tolist())
        baseline_missing=poly.difference(full_union).area
        covered=shapely.GeometryCollection()
        binding=None
        for i in indices:
            covered=covered.union(wins.loc[i])
            if poly.difference(covered).area<=max(1e-6,baseline_missing+1e-6):
                binding=float(sc.loc[i]);break
        records.append({'grid_id':grid,'poly_id':int(pid),'area_m2':poly.area,'cls_applied':bool(polys.loc[pid].cls_applied),'baseline_missing_m2':baseline_missing,'full_coverage_binding':binding,'any_window_binding':float(sc.loc[indices[0]]),'chip_indices':indices,'chip_scores':[float(sc.loc[i]) for i in indices],'poly':poly,'wins':[wins.loc[i] for i in indices]})
    print(grid, 'objects', len(polys),flush=True)
flat=[{k:v for k,v in r.items() if k not in ['poly','wins','chip_indices','chip_scores']} for r in records]
pd.DataFrame(flat).to_csv(OUT/'dino_all_reference_coverage_bindings.csv',index=False)
baseline_tau=json.loads((ROOT/'train/interim_tau_vit30_folds0to3.json').read_text())['tau']
thresholds=[0,baseline_tau,.025,.05,.1,.2,.2956995517015457,.3,.4,.45,.5,.6,.7,.8,.9,.95,.97,.98,.99,.995]
binding_min=min(r['full_coverage_binding'] for r in records)
non_suspect_bound=min(r['full_coverage_binding'] for r in records if (r['grid_id'],r['poly_id']) not in {('CPT2390',1),('CPT2216',2),('CPT2997',1),('CPT2997',16)})
thresholds=sorted(set(thresholds+[binding_min,non_suspect_bound]))
rows=[]
affected=[]
for tau in thresholds:
    kept=scores.vit30>=tau
    partial=whole=0;missing_sum=0
    for r in records:
        if r['full_coverage_binding']>=tau:continue
        kept_windows=[w for w,s in zip(r['wins'],r['chip_scores']) if s>=tau]
        missing=r['poly'].difference(shapely.union_all(kept_windows)).area
        extra=max(0,missing-r['baseline_missing_m2'])
        if extra>1e-6:
            partial+=1;missing_sum+=extra
            lost=not kept_windows
            whole+=int(lost)
            affected.append({'threshold':tau,**{k:r[k] for k in ['grid_id','poly_id','area_m2','cls_applied']},'extra_missing_m2':extra,'whole_install_lost':lost,'full_coverage_binding':r['full_coverage_binding']})
    rows.append({'threshold':tau,'total_chips':len(scores),'kept_chips':int(kept.sum()),'skipped_chips':int((~kept).sum()),'skipped_pct':float((~kept).mean()*100),'reference_positive_chips_skipped':int((~kept & scores.y_post).sum()),'reference_objects_with_extra_missing_area':partial,'reference_objects_with_no_surviving_window':whole,'extra_missing_area_m2':missing_sum})
pd.DataFrame(rows).to_csv(OUT/'dino_threshold_sweep.csv',index=False)
pd.DataFrame(affected).to_csv(OUT/'dino_threshold_affected_objects.csv',index=False)
summary={'scope':'Cape Town 26 grids, folds 0-3 only','reference_objects':len(records),'no_additional_reference_area_loss_max_threshold':binding_min,'four_suspect_objects_excluded_DIAGNOSTIC_ONLY_max_threshold':non_suspect_bound,'baseline_uncovered_objects':sum(r['baseline_missing_m2']>1e-6 for r in records),'coverage_tolerance_m2':1e-6,'densify_step_degrees':.000008,'notes':'References are detector-derived. Coverage is relative to original windows; no labels excluded or modified. No downstream inference rerun. Small objects require full coverage in this analysis.'}
(OUT/'dino_threshold_sweep_summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
print(pd.DataFrame(rows).to_string(index=False))
