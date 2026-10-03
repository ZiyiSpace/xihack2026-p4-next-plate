"use client";
import { Camera, ScanEye } from "lucide-react";
import type { Capture } from "@/lib/domain";

/** 判读结果的中文说法；未列出的分类原样显示，不会静默丢弃。 */
const classificationText:Record<string,string>={
 baseline:"首次上盘基准",
 no_change:"无明显变化",
 removal:"取用",
 refill_confirmed:"补菜已确认",
 late_refill:"补菜（记录迟到）",
 unexplained_increase:"未解释突增",
 anomaly_resolved:"异常已恢复",
 merged_pass:"同一次经过",
 out_of_order:"迟到旧事件",
 no_weight:"仅记录，无称重",
};

/** 判读结果对应的状态样式；异常要显眼，其余保持中性。 */
const classificationTone:Record<string,string>={
 removal:"watch",
 refill_confirmed:"okay",
 late_refill:"okay",
 anomaly_resolved:"okay",
 unexplained_increase:"urgent",
};

const clock=(s:string)=>new Date(s).toLocaleTimeString("zh-CN",{timeZone:"Asia/Shanghai",hour:"2-digit",minute:"2-digit",hour12:false});
const grams=(n:number)=>`${n.toLocaleString("zh-CN",{maximumFractionDigits:1})} 克`;

function Binding({capture}:{capture:Capture}){
 const check=capture.bindingCheck;
 if(!check)return <p className="capture-binding muted">本次未做视觉校验（未上传图片或菜品未绑定）。</p>;
 if(check.skipped)return <p className="capture-binding muted">视觉服务器未参与：{check.note||"离线规则模式"}</p>;
 const confidence=typeof check.confidence==="number"?check.confidence.toFixed(2):"—";
 const tone=check.ok?"is-okay":check.below_floor?"is-watch":"is-urgent";
 const verdict=check.ok?"与绑定一致":check.below_floor?"低于置信下限，不作判定":"与绑定不一致";
 return <p className={`capture-binding ${tone}`}>
  <ScanEye size={16}/>
  <span>模型识别 <b>{check.recognized||"未识别"}</b>（置信 {confidence}）· {verdict}</span>
  {!check.ok&&check.expected&&<small>盘上绑定的是 {check.expected}</small>}
 </p>;
}

/**
 * 采集抓拍：最近一次经过的照片、称重读数与规则判读。
 *
 * 数据来自采集端上报的站点事件（后端 `/api/state` 的 `captures`）。
 * 图片地址是同源相对 URL，直接当 src 用，不需要额外拼主机名。
 */
export default function CapturePanel({captures}:{captures?:Capture[]}){
 const list=captures??[];
 const latest=list[0];
 return <section className="panel capture-panel">
  <div className="panel-heading">
   <div><h2>采集抓拍</h2><p>转盘经过观察站时拍下的那一盘，连同称重读数和规则判读。</p></div>
   <span className="count-label">{list.length?`最近 ${list.length} 次`:"暂无记录"}</span>
  </div>
  {!latest
   ? <div className="capture-empty"><Camera size={26}/><p>还没有带图片的采集事件。启动虚拟采集端后，这里会显示刚拍到的画面。</p></div>
   : <>
    <div className="capture-body">
     <figure className="capture-shot">
      {latest.imageUrl
       ? /* 图片是同源相对地址，走原生 img 以免依赖图片优化服务 */
         <img src={latest.imageUrl} alt={`循环盘 ${latest.plateId} 在 ${latest.stationId} 站的抓拍`}/>
       : <div className="capture-noshot"><Camera size={22}/><span>该事件没有留下图片</span></div>}
      <figcaption><span>{latest.plateId} · {latest.stationId} 站</span><time>{clock(latest.at)}</time></figcaption>
     </figure>
     <div className="capture-read">
      <h3>{latest.dishName??latest.dishId??"未绑定菜品"}</h3>
      <p className="capture-verdict">
       <span className={`status ${classificationTone[latest.classification??""]||"watch"}`}>{classificationText[latest.classification??""]||latest.classification||"未判读"}</span>
       {latest.netWeightG!==null&&<span className="capture-weight">{grams(latest.netWeightG)}</span>}
       {latest.simulated&&<span className="capture-simulated">模拟数据</span>}
      </p>
      <Binding capture={latest}/>
      {latest.note&&<p className="capture-note">{latest.note}</p>}
     </div>
    </div>
    {list.length>1&&<ol className="capture-strip">
     {list.slice(0,8).map((c,i)=><li key={c.eventId} className={i===0?"is-latest":""}>
      {c.imageUrl?<img src={c.imageUrl} alt={`${c.plateId} ${clock(c.at)} 的抓拍`}/>:<span className="capture-noshot"><Camera size={16}/></span>}
      <span>{clock(c.at)}</span>
     </li>)}
    </ol>}
   </>}
 </section>;
}
