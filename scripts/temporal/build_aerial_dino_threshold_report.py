"""Publish diagnostic DINO threshold savings and a candidate chip manifest."""
from pathlib import Path
import base64
import html
import json
import pandas as pd
ROOT=Path('/home/gao/zasolar_data/geid_temporal/aerial_gate_tail_review_20261002')
INPUT=Path('/home/gao/zasolar_data/gate_pilot_2026-09/learning_curve_2026-09-28')
sweep=pd.read_csv(ROOT/'dino_threshold_sweep.csv')
scores=pd.read_csv(ROOT/'paired_scores.csv')
affected=pd.read_csv(ROOT/'dino_threshold_affected_objects.csv')
assert len(scores)==53508 and not scores.duplicated(['grid_id','chip_index']).any()
assert sweep.kept_chips.is_monotonic_decreasing
assert (sweep.kept_chips+sweep.skipped_chips).eq(len(scores)).all()
tau=.45
candidate=scores.copy()
candidate['gate_arm']='DINOv2 vit30'
candidate['candidate_threshold']=tau
candidate['send_to_detector']=candidate.vit30>=tau
candidate.to_csv(ROOT/'dino_candidate_045_manifest.csv',index=False)
selected=sweep[sweep.threshold==tau].iloc[0].to_dict()
policy={'arm':'vit30','model_family':'DINOv2','candidate_threshold':tau,'status':'experimental_candidate_not_production_frozen','scope':'Cape Town 26 grids / folds 0-3','keep_rule':'score >= threshold','counts':selected,'suspect_reference_objects':[{'grid_id':g,'poly_id':i} for g,i in [('CPT2390',1),('CPT2216',2),('CPT2997',1),('CPT2997',16)]],'labels_modified':False,'production_config_modified':False,'next_reference_loss_threshold':.4630841314792633,'limitations':['Detector-derived references, not human truth.','No downstream paired inference or elapsed-time benchmark.','No fifth-fold or independent holdout scores.','Gate-negative reference-negative chips have not been exhaustively reviewed.']}
decision_path=ROOT/'dino_threshold_045.json'
if decision_path.exists():
    decision=json.loads(decision_path.read_text())
    assert decision['threshold']==tau
    policy.update(status=decision['status'],confirmed_threshold=tau,human_adjudications_file=decision['human_adjudications_file'],remaining_reference_objects=decision['remaining_reference_objects'],remaining_objects_with_additional_coverage_loss=decision['remaining_objects_with_additional_coverage_loss'])
    policy['human_rejected_reference_objects']=policy.pop('suspect_reference_objects')
(ROOT/'dino_candidate_policy.json').write_text(json.dumps(policy,indent=2))
rows=sweep.to_dict('records')
cols=['threshold','kept_chips','skipped_chips','skipped_pct','reference_positive_chips_skipped','reference_objects_with_extra_missing_area','reference_objects_with_no_surviving_window','extra_missing_area_m2']
headers=['阈值（保留 ≥ 阈值）','保留小图（张）','跳过小图（张）','跳过比例（%）','跳过参考阳性小图（张）','有新增覆盖缺口的对象（个）','无窗口存活的对象（个）','新增缺口面积（m²）']
table='<table><thead><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr></thead><tbody>'
for r in rows:
 vals=[f'{r["threshold"]:.9f}',f'{r["kept_chips"]:,}',f'{r["skipped_chips"]:,}',f'{r["skipped_pct"]:.2f}',str(r[cols[4]]),str(r[cols[5]]),str(r[cols[6]]),f'{r[cols[7]]:.4f}']
 table+='<tr'+(' class="selected"' if r['threshold']==tau else '')+'>'+''.join('<td>'+v+'</td>' for v in vals)+'</tr>'
table+='</tbody></table>'
parts=['''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DINO 阈值收益与覆盖</title><style>body{font:16px system-ui;max-width:1400px;margin:32px auto;padding:0 24px;color:#222}h1{font-size:24px}h2{font-size:20px}p{line-height:1.7}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:9px;text-align:right;border-bottom:1px solid #ddd}.selected{background:#e7eff5}.scroll{overflow:auto}.images{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}img{width:100%}small{line-height:1.5;display:block}</style><h1>DINO 阈值：能节省多少检测小图？</h1><p>开普敦 26 格、前四折；2026-09-28 评分；2026-10-02 诊断。总计 53,508 张小图、4,010 个检测器参考对象。阈值不是校准后的概率。</p><h2>本轮已确认阈值：0.45</h2><p>保留 8,890 张，跳过 44,618 张（83.39%）。与阈值 0.3 相比再跳过 2,348 张。负责人已人工查看五个对象，确认四个原约束对象无板、CPT3468/380 有板，并选定 0.45。人工结论单独保存，原始参考文件保留。排除这四个人工否定对象后，其余 4,006 个参考对象在 0.45 下没有新增覆盖损失；生产配置尚未修改。</p><p>保持所有原参考对象既有覆盖时，最高阈值是 0.025218529627，可跳过 28,906 张（54.02%）。根据本次人工核验排除四个无板对象后，覆盖诊断上限为 0.463084131479；0.45 保留真实安装 CPT3468/380，留有分数余量。表格仍显示未清理的原始参考损失，人工修订结论与原始统计分开保存。</p><div class="scroll">''',table,'''</div><p>新增覆盖缺口：保留窗口对整块参考多边形的覆盖，相对全部窗口的原覆盖减少了多少。包括 &lt;30 m² 小安装。10 个参考对象原窗口已有极小几何缺口，比较采用相对基线；不能声称原始绝对覆盖均为 100%。经纬边界加密步长 0.000008 度，面积容差 0.000001 m²。未重新运行下游检测，跳过比例不是总耗时降幅；独立留出数据未验证，参考阴性小图中的漏板未穷尽核验。</p><h2>人工核验：四个无板对象与真实安装 CPT3468/380</h2><p>以下是相交的原始小图，未画参考轮廓。图内若有其他安装，不等于当前参考对象正确；应结合对象几何核验。Flash 标签只显示已有复核结果，没有补跑或使用 Pro。</p>''']
review=pd.read_csv(ROOT/'final_review.csv').set_index(['grid_id','chip_index'])
for grid,pid in [('CPT2390',1),('CPT2216',2),('CPT2997',1),('CPT2997',16),('CPT3468',380)]:
 hits=pd.read_csv(INPUT/'chips'/grid/'intersections.csv')
 ids=sorted(hits.loc[(hits.layer=='post')&(hits.poly_id==pid),'chip_index'].unique())
 parts.append(f'<h2>{grid} / 参考对象 {pid}</h2><div class="images">')
 for i in ids:
  sc=scores.loc[(scores.grid_id==grid)&(scores.chip_index==i),'vit30'].iloc[0]
  im=INPUT/'chips'/grid/'images'/f'{i:05d}.png'
  label={'pv':'有板','absent':'无板','uncertain':'不能判'}.get(review.loc[(grid,i),'current_state'],'未复核') if (grid,i) in review.index else '未复核'
  parts.append(f'<div><img loading="lazy" src="data:image/png;base64,{base64.b64encode(im.read_bytes()).decode()}"><small>chip {i} · DINO {sc:.6f} · 0.45 下{"保留" if sc>=tau else "跳过"} · Flash 3.8：{label}</small></div>')
 parts.append('</div>')
parts.append('<p><a href="review_gallery.html">返回原低分复核图集</a> · <a href="dino_threshold_sweep.csv">下载阈值统计 CSV</a> · <a href="dino_candidate_045_manifest.csv">下载 0.45 候选清单</a></p></html>')
(ROOT/'dino_threshold_report.html').write_text(''.join(parts))
canvas='''import {Stack,H1,H2,Text,Table,Select,useCanvasState,useHostTheme} from "cursor/canvas";
const rows=__ROWS__;
export default function DinoThresholdSweep(){const theme=useHostTheme();const [chosen,setChosen]=useCanvasState("dino-tau",String(.45));const r=rows.find(x=>String(x.threshold)===chosen)||rows[0];return <Stack gap={18} style={{padding:24,color:theme.text.primary}}><H1>DINO 阈值收益与安装覆盖</H1><Text>开普敦 26 格 · 53,508 小图 · 4,010 检测器参考对象 · 2026-09-28 评分，2026-10-02 核验。参考不是人工真值。</Text><Select value={chosen} onChange={setChosen} options={rows.map(x=>({value:String(x.threshold),label:x.threshold.toFixed(9)}))}/><H2>跳过 {r.skipped_chips.toLocaleString()} 张（{r.skipped_pct.toFixed(2)}%），保留 {r.kept_chips.toLocaleString()} 张</H2><Text>该阈值使 {r.reference_objects_with_extra_missing_area} 个参考对象新增覆盖缺口，其中 {r.reference_objects_with_no_surviving_window} 个没有窗口存活。新增缺口面积 {r.extra_missing_area_m2.toFixed(4)} m²。</Text><Text>负责人已人工确认四个原约束对象无板、CPT3468/380 有板，并选定本轮阈值 0.45。排除四个人工否定对象后，其余 4,006 个参考对象无新增覆盖损失，真实安装 380 仍保留。表中仍为原始参考统计；下一覆盖约束分数 0.463084。</Text><Table headers={["阈值","保留（张）","跳过（张）","跳过比例（%）","新增缺口对象（个）","无存活窗口对象（个）"]} rows={rows.map(x=>[x.threshold.toFixed(9),x.kept_chips,x.skipped_chips,x.skipped_pct.toFixed(2),x.reference_objects_with_extra_missing_area,x.reference_objects_with_no_surviving_window])}/><Text>全参考多边形覆盖，包括小于 30 m² 对象；相对全窗口基线，面积容差 0.000001 m²，经纬边界加密 0.000008 度。未跑下游推理和时间测量，跳过比例不能等同于总耗时下降。独立留出与参考阴性漏板未验证。</Text></Stack>}
'''
Path('/home/gao/.cursor/projects/home-gao-projects-solar-backdating/canvases/dino-threshold-sweep.canvas.tsx').write_text(canvas.replace('__ROWS__',json.dumps(rows)))
print(json.dumps({'candidate':selected,'page_bytes':(ROOT/'dino_threshold_report.html').stat().st_size},indent=2))
