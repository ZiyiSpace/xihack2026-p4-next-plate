"use client";
import { useCallback, useRef, useState } from "react";
import { AlertTriangle, Check, Images, Loader2, Play, Trash2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "sonner";
import type { CaptureBindingCheck, DishSummary, SampleList, SampleRow } from "@/lib/domain";

/** 后端判读分类的中文说法；未列出的原样显示，不静默丢掉。 */
const classificationText:Record<string,string>={
 baseline:"首次上盘基准", no_change:"无明显变化", removal:"取用",
 refill_confirmed:"补菜已确认", late_refill:"补菜（记录迟到）",
 unexplained_increase:"未解释突增", anomaly_resolved:"异常已恢复",
 merged_pass:"同一次经过", out_of_order:"迟到旧事件", no_weight:"仅记录，无称重",
};

const AUTO = "__auto__";
const grams=(n:number|null|undefined)=>n===null||n===undefined?"—":`${n.toLocaleString("zh-CN",{maximumFractionDigits:1})} 克`;
const clock=(s:string)=>new Date(s).toLocaleTimeString("zh-CN",{timeZone:"Asia/Shanghai",hour:"2-digit",minute:"2-digit",hour12:false});

/** 识别结果怎么说给用户听。识别不可信时明确说不判定，不糊弄过去。 */
function recognizedText(check:CaptureBindingCheck|null){
 if(!check)return null;
 if(check.skipped)return {tone:"",text:`未识别：${check.note||"视觉服务器未参与"}`};
 if(!check.recognized)return {tone:"",text:"模型没有给出菜品"};
 const conf=typeof check.confidence==="number"?check.confidence.toFixed(2):"—";
 if(check.below_floor)return {tone:"is-watch",text:`识别为 ${check.recognized}（${conf}）· 置信不足，不判定`};
 return {tone:"is-okay",text:`识别为 ${check.recognized}（${conf}）`};
}

function Result({row}:{row:SampleRow}){
 const r=row.result;
 if(!r)return null;
 if(r.error)return <span className="sample-state is-error"><AlertTriangle size={14}/>{r.error}</span>;
 const parts=[classificationText[r.classification||""]||r.classification||"已记录"];
 if(r.taken_g)parts.push(`取用 ${grams(r.taken_g)}`);
 if(r.new_tasks?.length)parts.push("已生成补菜任务");
 return <span className="sample-state is-done"><Check size={14}/>{parts.join(" · ")}</span>;
}

/**
 * 样例数据接入：把「图片 + 称重」传上来，点运行走完整条链路。
 *
 * 它不另开旁路——入账走的是和真实采集端同一个站点事件接口，
 * 所以工作台的余量、台账、补菜任务、时段分析会自动带上这些样例。
 * 清单本身由工作台的 /api/state 轮询带出（`data.samples`），这里不做第二份状态。
 */
export default function SampleIntake({dishes,list,onChanged}:{
 dishes:DishSummary[]; list?:SampleList; onChanged:()=>void;
}){
 const [busy,setBusy]=useState(false);
 const [phase,setPhase]=useState("");
 const [error,setError]=useState("");
 const fileInput=useRef<HTMLInputElement>(null);
 const dishName=useCallback((id:string)=>dishes.find(d=>d.id===id)?.name||id,[dishes]);

 const send=useCallback(async(path:string,init?:RequestInit)=>{
  const response=await fetch(path,init);
  const result=await response.json() as SampleList & {error?:string};
  if(!response.ok)throw new Error(result.error||`请求失败（${response.status}）`);
  return result;
 },[]);

 const upload=useCallback(async(files:FileList)=>{
  if(!files.length)return;
  setBusy(true);setError("");
  try{
   const form=new FormData();
   for(const f of Array.from(files))form.append("files",f);
   // 重量留到列表里逐行填，上传时只登记图片
   form.append("items",JSON.stringify(Array.from(files).map(()=>({}))));
   const result=await send("/api/samples",{method:"POST",body:form});
   toast.success(`已加入 ${result.samples.length} 条样例，填好净重后点运行`);
   onChanged();
  }catch(e){setError(e instanceof Error?e.message:"上传失败");}
  finally{setBusy(false);if(fileInput.current)fileInput.current.value="";}
 },[send,onChanged]);

 const patch=useCallback(async(id:string,body:Record<string,unknown>)=>{
  try{await send(`/api/samples/${id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});onChanged();}
  catch(e){setError(e instanceof Error?e.message:"保存失败");}
 },[send,onChanged]);

 const remove=useCallback(async(id:string)=>{
  try{await send(`/api/samples/${id}`,{method:"DELETE"});onChanged();}
  catch(e){setError(e instanceof Error?e.message:"删除失败");}
 },[send,onChanged]);

 const clearAll=useCallback(async()=>{
  try{await send("/api/samples",{method:"DELETE"});toast.success("样例已清空");onChanged();}
  catch(e){setError(e instanceof Error?e.message:"清空失败");}
 },[send,onChanged]);

 const run=useCallback(async()=>{
  if(!list?.samples.length)return;
  setBusy(true);setError("");
  try{
   // 第一步：还没指定菜品的逐条送进视觉接口识别，这样进度看得见、失败也只影响一条
   const pending=list.samples.filter(s=>!s.dish_id&&s.status!=="identified");
   for(let i=0;i<pending.length;i++){
    setPhase(`识别中 ${i+1}/${pending.length}`);
    await send(`/api/samples/${pending[i].sample_id}/identify`,{method:"POST"});
   }
   // 第二步：一次性入账。后端按菜品自动分盘，同一道菜的多张图共用一个循环盘
   setPhase("入账中…");
   const result=await send("/api/samples/ingest",{method:"POST"}) as unknown as {ingested:number;blocked:{reason:string}[]};
   if(result.blocked?.length)toast.warning(`${result.ingested} 条已入账，${result.blocked.length} 条因识别不可信被挡下——请手动指定菜品后重试`);
   else if(result.ingested)toast.success(`${result.ingested} 条已入账，数据已进入工作台分析口径`);
   else toast.warning("没有可入账的样例，请先填净重或指定菜品");
  }catch(e){setError(e instanceof Error?e.message:"运行失败");}
  finally{setBusy(false);setPhase("");onChanged();}
 },[list,send,onChanged]);

 const samples=list?.samples??[];
 const counts=list?.counts;
 const ready=samples.filter(s=>s.net_weight_g!==null).length;

 return <section className="panel sample-intake">
  <div className="panel-heading">
   <div><h2>样例数据接入</h2><p>手上还没有相机和称重台时，把「一张图 + 一个净重」传进来。点运行会走完整条链路：视觉接口识别菜品 → 记入余量 → 触发补菜任务，结果直接进入工作台的分析口径。</p></div>
   <span className="count-label">{counts?`${counts.total} 条 · ${counts.done} 条已入账`:"正在读取"}</span>
  </div>
  <div className="sample-toolbar">
   <label className={`sample-pick ${busy?"is-disabled":""}`}>
    <input ref={fileInput} type="file" accept="image/*" multiple disabled={busy}
           onChange={e=>e.target.files&&void upload(e.target.files)}/>
    <Upload size={16}/>选择图片
   </label>
   <Button disabled={busy||!samples.length} onClick={()=>void run()}>
    {busy?<Loader2 className="animate-spin" size={16}/>:<Play size={16}/>}{phase||"运行"}
   </Button>
   <Button variant="ghost" disabled={busy||!samples.length} onClick={()=>void clearAll()}><Trash2 size={16}/>清空</Button>
   <span className="sample-hint">
    {samples.length?`${ready}/${samples.length} 条已填净重`:"还没有样例。选几张图，逐行填净重即可。"}
   </span>
  </div>
  {error&&<p role="alert" className="sample-error"><AlertTriangle size={15}/>{error}</p>}
  {!samples.length
   ? <div className="sample-empty"><Images size={26}/><p>上传图片后会出现在这里。不填盘号时，运行时会按识别出的菜品自动分盘——同一道菜的多张图共用一个盘，系统才算得出「取用」和补菜。</p></div>
   : <Table mobileLayout="cards">
      <TableHeader><TableRow>
       <TableHead>图片</TableHead><TableHead>净重 / 克</TableHead><TableHead>盘号</TableHead>
       <TableHead>站点</TableHead><TableHead>菜品</TableHead><TableHead>状态</TableHead><TableHead/>
      </TableRow></TableHeader>
      <TableBody>{samples.map(s=>{
       const rec=recognizedText(s.recognized);
       return <TableRow key={s.sample_id}>
        <TableCell data-label="图片">
         <div className="sample-file">
          <img src={s.image_ref} alt={s.image_name} className="sample-thumb"/>
          <span><b>{s.image_name}</b><small className="cell-meta">{clock(s.observed_at)} 采集</small></span>
         </div>
        </TableCell>
        <TableCell data-label="净重 / 克">
         <Input type="number" min={0} step="1" inputMode="decimal" aria-label={`${s.image_name} 的净重`}
                defaultValue={s.net_weight_g??""} placeholder="称重读数" disabled={busy}
                onBlur={e=>{const v=e.target.value;if(String(s.net_weight_g??"")!==v)void patch(s.sample_id,{net_weight_g:v});}}/>
        </TableCell>
        <TableCell data-label="盘号">
         <Input aria-label={`${s.image_name} 的盘号`} defaultValue={s.plate_id} placeholder="自动" disabled={busy}
                onBlur={e=>{if(e.target.value!==s.plate_id)void patch(s.sample_id,{plate_id:e.target.value});}}/>
        </TableCell>
        <TableCell data-label="站点">
         <Input aria-label={`${s.image_name} 的站点`} defaultValue={s.station_id} maxLength={4} disabled={busy}
                onBlur={e=>{if(e.target.value!==s.station_id)void patch(s.sample_id,{station_id:e.target.value});}}/>
        </TableCell>
        <TableCell data-label="菜品">
         <Select value={s.dish_id||AUTO} disabled={busy}
                 onValueChange={v=>void patch(s.sample_id,{dish_id:v===AUTO?"":v})}>
          <SelectTrigger aria-label={`${s.image_name} 的菜品`}><SelectValue/></SelectTrigger>
          <SelectContent>
           <SelectItem value={AUTO}>由模型识别</SelectItem>
           {dishes.map(d=><SelectItem value={d.id} key={d.id}>{d.name}</SelectItem>)}
          </SelectContent>
         </Select>
        </TableCell>
        <TableCell data-label="状态">
         <div className="sample-status">
          <Result row={s}/>
          {!s.result&&rec&&<span className={`sample-state ${rec.tone}`}>{rec.text}</span>}
          {!s.result&&!rec&&<span className="sample-state">{s.dish_id?`已指定 ${dishName(s.dish_id)}`:"待运行"}</span>}
         </div>
        </TableCell>
        <TableCell><Button variant="ghost" size="sm" aria-label={`移除 ${s.image_name}`} disabled={busy}
                           onClick={()=>void remove(s.sample_id)}><Trash2 size={15}/></Button></TableCell>
       </TableRow>;
      })}</TableBody>
     </Table>}
  <p className="sample-note">样例走的是和真实采集端同一个站点事件接口，所以不需要额外导入：入账之后，总览的余量、台账、补菜任务和分析页的时段统计都会带上它。</p>
 </section>;
}
