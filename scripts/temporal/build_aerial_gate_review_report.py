from pathlib import Path
import base64
import hashlib
import html
import json

import numpy as np
import pandas as pd

ROOT = Path('/home/gao/zasolar_data/geid_temporal/aerial_gate_tail_review_20261002')
CANVAS = Path('/home/gao/.cursor/projects/home-gao-projects-solar-backdating/canvases/aerial-gate-tail-review.canvas.tsx')

lite = pd.read_csv(ROOT/'gemini_review.csv')
current = pd.read_csv(ROOT/'flash38_recheck/gemini_review.csv')
assert len(lite) == len(current) == 320
assert current.requested_model.eq('gemini-3.8-flash').all()
assert not current.returned_model.fillna('').str.contains('pro',case=False).any()
keys = ['review_id', 'grid_id', 'chip_index', 'image_sha256']
fields = ['pv_present', 'confidence', 'quality_flag', 'scene', 'evidence', 'notes', 'requested_model', 'returned_model']
df = lite.rename(columns={f:f'lite_{f}' for f in fields}).merge(
    current[keys+fields].rename(columns={f:f'current_{f}' for f in fields}), on=keys,
    how='outer', validate='one_to_one', indicator=True)
assert df['_merge'].eq('both').all()
df = df.drop(columns='_merge').merge(pd.read_csv(ROOT/'reference_visible_area.csv'),
                                    on=['review_id','grid_id','chip_index'],validate='one_to_one')
df['reference_visible_sum_m2'] = df.reference_visible_sum_m2.fillna(0)

def state(value):
    if pd.isna(value): return 'uncertain'
    return 'pv' if str(value).lower() == 'true' else 'absent'

df['lite_state'] = df.lite_pv_present.map(state)
df['current_state'] = df.current_pv_present.map(state)
df['model_disagreement'] = df.lite_state != df.current_state
df.to_csv(ROOT/'final_review.csv',index=False)
queue = df[df.model_disagreement | df.current_state.ne('absent')].copy()
queue.to_csv(ROOT/'priority_review_queue.csv',index=False)
summary = {'review_n':len(df), 'low_tail_n':int(df.review_group.eq('low_tail').sum()),
           'model_disagreements_all':int(df.model_disagreement.sum()),
           'model_disagreements_low':int((df.model_disagreement & df.review_group.eq('low_tail')).sum()),
           'priority_queue_n':len(queue), 'current_returned_model':sorted(df.current_returned_model.dropna().unique().tolist()),
           'active_review_model':'gemini-3.8-flash', 'retired_models':['Gemini Pro'], 'groups':{}}
for name, mask in [('low_tail',df.review_group.eq('low_tail')),('dino_low',df.dino_low),('yolo_low',df.yolo_low),('controls',df.review_group.ne('low_tail'))]:
    sub=df[mask]
    summary['groups'][name]={'n':len(sub),**{s:int(sub.current_state.eq(s).sum()) for s in ['pv','absent','uncertain']}}
    summary['groups'][name]['reference_area_under_1m2']=int((sub.reference_visible_sum_m2<1).sum())
summary['priority_queue_sha256']=hashlib.sha256((ROOT/'priority_review_queue.csv').read_bytes()).hexdigest()
(ROOT/'final_summary.json').write_text(json.dumps(summary,indent=2))

records=[]
for row in df.sort_values(['vit30','yolo30']).itertuples():
    records.append(dict(id=row.review_id,grid=row.grid_id,chip=int(row.chip_index),dino=round(row.vit30,6),
                        yolo=round(row.yolo30,6),dinoLow=bool(row.dino_low),yoloLow=bool(row.yolo_low),
                        group=row.review_group,lite=row.lite_state,current=row.current_state,
                        disagreement=bool(row.model_disagreement),area=round(row.reference_visible_sum_m2,6),
                        evidence=row.current_evidence,quality=row.current_quality_flag))

# Standalone local gallery: original image bytes, score metadata, both model labels.
parts=['''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>航拍前置门低分复核 · 2026-10-02</title><style>
body{font:16px/1.6 system-ui;margin:24px;background:#fafafa;color:#242424;max-width:1600px}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:18px}
article{border:1px solid #ddd;padding:12px;background:white}img{width:100%;height:auto}h1{font-size:24px}h2{font-size:18px}
label{margin-right:16px}small{color:#555}p{margin:6px 0}</style>
<h1>航拍前置门低分小图 · 开普敦 26 格</h1>
<p>302 张低分候选 + 18 张对照，旧 Lite 与当前 Flash 3.8 均已看完。分界只是探索性复核边界，模型标签不是人工真值。本次范围仅为开普敦。</p>
<p>Flash 3.8 请求路由：gemini-3.8-flash；接口返回的模型名称保存在逐图 CSV 中；名称不能独立证明底层模型版本。Pro 已停用，旧结果仅存档。图像无标记，判断整张图任何位置是否有板。</p>
<p><a href="dino_threshold_report.html">查看 DINO 阈值收益与完整覆盖核验</a></p>
<label>队列 <select id="arm"><option value="all">全部</option><option value="dino">DINO 低分</option><option value="yolo">YOLO 低分</option><option value="controls">对照</option></select></label>
<label>Flash 3.8 判断 <select id="verdict"><option value="all">全部</option><option value="pv">有板</option><option value="absent">无板</option><option value="uncertain">不能判</option></select></label>
<label><input type="checkbox" id="disagree">只看两轮分歧</label><p id="count"></p><section class="gallery">''']
words={'pv':'有板','absent':'无板','uncertain':'不能判'}
for r in records:
    image=Path(df.loc[df.review_id.eq(r['id']),'image_path'].iloc[0])
    src='data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode()
    attrs=f'data-dino="{int(r["dinoLow"])}" data-yolo="{int(r["yoloLow"])}" data-control="{int(r["group"]!="low_tail")}" data-verdict="{r["current"]}" data-disagree="{int(r["disagreement"])}"'
    parts.append(f'<article {attrs}><h2>{r["grid"]} / chip {r["chip"]}</h2><img src="{src}" loading="lazy"><p>DINO {r["dino"]:.6f} · YOLO {r["yolo"]:.6f}</p><p>Lite：{words[r["lite"]]} · Flash 3.8：{words[r["current"]]} · 两轮{"分歧" if r["disagreement"] else "一致"}</p><p>参考重叠面积加总 {r["area"]:.6g} m²；不是安装总面积。</p><small>{html.escape(r["evidence"])}</small></article>')
parts.append('''</section><script>function update(){let n=0;const arm=document.getElementById('arm').value,v=document.getElementById('verdict').value,d=document.getElementById('disagree').checked;document.querySelectorAll('article').forEach(a=>{const ok=(arm==='all'||(arm==='controls'?a.dataset.control==='1':a.dataset[arm]==='1'))&&(v==='all'||a.dataset.verdict===v)&&(!d||a.dataset.disagree==='1');a.hidden=!ok;if(ok)n++;});document.getElementById('count').textContent='显示 '+n+' / 320 张';}document.querySelectorAll('select,input').forEach(e=>e.addEventListener('change',update));update();</script></html>''')
(ROOT/'review_gallery.html').write_text(''.join(parts))

scores=pd.read_csv(ROOT/'paired_scores.csv')
series={arm:np.sort(scores.loc[scores.y_post,arm].values)[:400][::10].round(6).tolist() for arm in ['vit30','yolo30']}
template='''import {Stack,Grid,Row,H1,H2,Text,Table,LineChart,Select,TextInput,useCanvasState,useHostTheme} from "cursor/canvas";
const data=__DATA__;
const summary=__SUMMARY__;
const lines=__LINES__;
const stateName=(s:string)=>s==="pv"?"有板":s==="absent"?"无板":"不能判";
export default function AerialGateReview(){
 const theme=useHostTheme();
 const [arm,setArm]=useCanvasState("aerial-gate-arm","all");
 const [verdict,setVerdict]=useCanvasState("aerial-gate-verdict","all");
 const [query,setQuery]=useCanvasState("aerial-gate-query","");
 const rows=data.filter(r=>(arm==="all"||(arm==="dino"?r.dinoLow:arm==="yolo"?r.yoloLow:r.disagreement))&&(verdict==="all"||r.current===verdict)&&(!query||(`${r.grid} ${r.chip} ${r.evidence}`).toLowerCase().includes(query.toLowerCase())));
 const groups=summary.groups;
 return <Stack gap={20} style={{padding:24,color:theme.text.primary}}>
  <H1>航拍前置门：低分复核</H1>
  <Text>开普敦 26 格 · 53,508 张配对小图 · 参考阳性 4,282 张 · 审计日期 2026-10-02。本次范围仅为开普敦。</Text>
  <Text>全量分数没有特别大的间隙。参考阳性中的最大间隙仅作为复核边界：DINO 0.278911–0.312488，YOLO 0.213494–0.220636。不能作为生产删除阈值。</Text>
  <Table headers={["复核队列（有重合）","张数","Flash 3.8：有板","Flash 3.8：无板","Flash 3.8：不能判"]} rows={[["DINO 低分",groups.dino_low.n,groups.dino_low.pv,groups.dino_low.absent,groups.dino_low.uncertain],["YOLO 低分",groups.yolo_low.n,groups.yolo_low.pv,groups.yolo_low.absent,groups.yolo_low.uncertain],["去重低分",groups.low_tail.n,groups.low_tail.pv,groups.low_tail.absent,groups.low_tail.uncertain],["对照",groups.controls.n,groups.controls.pv,groups.controls.absent,groups.controls.uncertain]]}/>
  <Text>判断来源：旧 Gemini 3.1 Lite（历史对照）与当前 Gemini 3.8 Flash 各看 320 张，未提供原参考标签与小模型分数。Flash 3.8 请求为 gemini-3.8-flash，返回模型名保存在逐图 CSV 中。Pro 已停用，其旧结果不作当前结论。Gemini 标签是模型意见，不能当作人工真值。</Text>
  <Grid columns={2} gap={24}>
   <Stack><H2>DINO：参考阳性低分排序</H2><Text>横轴：升序名次（张）；纵轴：小图分数（0–1，未校准）。</Text><LineChart categories={lines.vit30.map((_,i)=>String(i*10+1))} series={[{name:"DINOv2",data:lines.vit30,tone:"info"}]} referenceLines={[{value:0.29569955,label:"探索性复核分界"}]} height={230}/></Stack>
   <Stack><H2>YOLO：参考阳性低分排序</H2><Text>横轴：升序名次（张）；纵轴：小图分数（0–1，未校准）。</Text><LineChart categories={lines.yolo30.map((_,i)=>String(i*10+1))} series={[{name:"YOLO30",data:lines.yolo30,tone:"neutral"}]} referenceLines={[{value:0.21706489,label:"探索性复核分界"}]} height={230}/></Stack>
  </Grid>
  <Text>图源：2026-09-28 开普敦前四折评分。展示参考阳性最低 400 张，每 10 张取一个排序点；未经概率校准。YOLO 零分含低于 0.001 的未出框结果。</Text>
  <H2>分歧与边缘重叠</H2>
  <Text>两轮在低分组有 {summary.model_disagreements_low} 张分歧。302 张低分图里 137 张与参考多边形重叠不足 1 m²；图上无板不能直接推出整块安装是假阳性。不核验这些情况会继续让错误标签压低阈值，或者在提高阈值时误删真实光伏。</Text>
  <Row gap={12} wrap><Select value={arm} onChange={setArm} options={[{value:"all",label:"全部队列"},{value:"dino",label:"DINO 低分"},{value:"yolo",label:"YOLO 低分"},{value:"disagreement",label:"两轮分歧"}]}/><Select value={verdict} onChange={setVerdict} options={[{value:"all",label:"全部 Flash 3.8 判断"},{value:"pv",label:"有板"},{value:"absent",label:"无板"},{value:"uncertain",label:"不能判"}]}/><TextInput value={query} onChange={setQuery} placeholder="搜索网格、chip 编号或证据"/></Row>
  <Text>匹配 {rows.length} 张，显示前 80 张；全量 CSV 与离线图集在数据目录。重叠面积是参考对象与小图交集面积的加总，不是安装总面积。</Text>
  {rows.length>0&&<Table headers={["网格 / chip","DINO / YOLO","Lite / Flash 3.8","参考重叠 m²","Flash 3.8 视觉证据"]} rows={rows.slice(0,80).map(r=>[`${r.grid} / ${r.chip}`,`${r.dino.toFixed(4)} / ${r.yolo.toFixed(4)}`,`${stateName(r.lite)} / ${stateName(r.current)}`,r.area.toFixed(4),r.evidence])}/>}
 </Stack>;
}
'''
CANVAS.write_text(template.replace('__DATA__',json.dumps(records,ensure_ascii=False)).replace('__SUMMARY__',json.dumps(summary,ensure_ascii=False)).replace('__LINES__',json.dumps(series)))
print(json.dumps(summary,indent=2))
print('Canvas',CANVAS)
