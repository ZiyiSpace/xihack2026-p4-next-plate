import { emptyImports, effectiveCost } from "./cost.ts";
import type { Dish, DishSummary, Observation, Source, State, Summary, Ledger, Task } from "./domain";
const minute=60000;
export const shanghaiDate=(s:string)=>new Date(new Date(s).getTime()+8*3600000).toISOString().slice(0,10);
export const shanghaiTime=(s:string)=>new Date(new Date(s).getTime()+8*3600000).toISOString().slice(11,16);
const time=(s:string)=>new Date(s).getTime();
export const uid=()=>crypto.randomUUID();
const baseDishes:Omit<Dish,"confirmed">[]=[
 {id:"beef",name:"精选肥牛",category:"荤菜",unit:"g",capacity:400,batch:600,target:1800,leadMinutes:5,safetyMinutes:3,costPerKg:48},
 {id:"shrimp",name:"鲜虾",category:"荤菜",unit:"件",capacity:20,batch:20,target:60,leadMinutes:6,safetyMinutes:3,costPerKg:42},
 {id:"potato",name:"土豆片",category:"蔬菜",unit:"g",capacity:400,batch:400,target:1600,leadMinutes:3,safetyMinutes:3,costPerKg:4},
 {id:"tofu",name:"嫩豆腐",category:"豆制品",unit:"g",capacity:400,batch:400,target:1800,leadMinutes:3,safetyMinutes:3,costPerKg:6},
 {id:"mushroom",name:"金针菇",category:"蔬菜",unit:"g",capacity:300,batch:300,target:1400,leadMinutes:3,safetyMinutes:3,costPerKg:9},
 {id:"lettuce",name:"生菜",category:"蔬菜",unit:"g",capacity:250,batch:250,target:1200,leadMinutes:2,safetyMinutes:3,costPerKg:7},
 {id:"meatball",name:"牛肉丸",category:"荤菜",unit:"件",capacity:16,batch:16,target:64,leadMinutes:5,safetyMinutes:3,costPerKg:28},
 {id:"kelp",name:"海带结",category:"蔬菜",unit:"g",capacity:350,batch:350,target:1400,leadMinutes:3,safetyMinutes:3,costPerKg:8},
 {id:"lotus",name:"莲藕片",category:"蔬菜",unit:"g",capacity:350,batch:350,target:1400,leadMinutes:3,safetyMinutes:3,costPerKg:8},
 {id:"watermelon",name:"西瓜",category:"水果点心",unit:"件",capacity:12,batch:12,target:48,leadMinutes:4,safetyMinutes:3,costPerKg:5},
 {id:"bun",name:"小馒头",category:"水果点心",unit:"件",capacity:10,batch:10,target:40,leadMinutes:7,safetyMinutes:3,costPerKg:12},
 {id:"noodle",name:"鲜面条",category:"主食",unit:"g",capacity:300,batch:300,target:1200,leadMinutes:3,safetyMinutes:3,costPerKg:5}
];
function record(s:State,l:Omit<Ledger,"id"|"costPerKg">){s.ledger.push({...l,id:uid(),costPerKg:effectiveCost(s,s.dishes.find(d=>d.id===l.dishId)!)});}
export function createState(source:Source):State {
 const clock=source==="demo"?"2026-10-03T18:42:00+08:00":new Date().toISOString();
 const s:State={source,clock,dishes:baseDishes.map(d=>({...d,confirmed:source==="demo"})),plates:{},ledger:[],tasks:[],orders:[],seenEventIds:[],actionIds:[],settings:{autoEnabled:source==="demo",closeTime:"22:00",staleMinutes:5},revision:0,imports:emptyImports()};
 if(source!=="demo")return s;
 // Synthetic history is a reproducible demo scenario, not inferred store evidence.
 for(let h=11;h<=18;h++) {
  const guests=[18,40,32,12,8,12,28,44][h-11];
  s.orders.push({id:`demo-order-${h}`,at:`2026-10-03T${h}:00:00+08:00`,guests,revenue:guests*49,groupSize:h>=17?3:2});
  s.dishes.forEach((d,i)=>{
   const factor=[1.7,1.3,1.15,1.05,.9,.8,1.15,.7,.9,1.1,.65,1][i];
   const weight=Math.round(guests*factor*(d.category==="荤菜"?23:18));
   const q=d.unit==="件"?Math.round(weight/(d.id==="watermelon"?45:25)):weight;
   record(s,{at:`2026-10-03T${h}:25:00+08:00`,dishId:d.id,plateId:`history-${i}`,kind:"take",quantity:q,weightG:weight});
   for(let n=0;n<Math.ceil(q/d.batch);n++)record(s,{at:`2026-10-03T${h}:10:00+08:00`,dishId:d.id,plateId:`history-${i}`,kind:"refill",quantity:d.batch,weightG:d.unit==="件"?d.batch*(d.id==="watermelon"?45:25):d.batch});
   if(h===14||h===17){const waste=Math.round(weight*(i===7||i===10?.18:.035));record(s,{at:`2026-10-03T${h}:40:00+08:00`,dishId:d.id,plateId:`withdraw-${i}`,kind:"waste",quantity:d.unit==="件"?Math.round(waste/25):waste,weightG:waste,reason:"演示记录：长时间未取用"});}
  });
 }
 const quantities=[210,4,150,1280,980,850,48,1200,1100,36,30,920];
 s.dishes.forEach((d,i)=>{
  const qty=quantities[i];const n=Math.ceil(qty/d.capacity);
  for(let p=0;p<n;p++){
   const q=Math.min(d.capacity,qty-p*d.capacity); const weight=d.unit==="g"?q:q*(d.id==="watermelon"?45:25);
   const id=`${d.id}-${p+1}`;
   s.plates[id]={id,dishId:d.id,quantity:q,weightG:weight,seenAt:s.clock,quality:.98,removed:false};
   record(s,{at:s.clock,dishId:d.id,plateId:id,kind:"opening",quantity:q,weightG:weight});
  }
  // Recent measured decreases give a rate, rather than a hard-coded forecast.
  for(let j=1;j<=6;j++){const q=d.unit==="g"?[70,1,50,27,18,16,1,12,16,1,1,20][i]:1;const weight=d.unit==="g"?q:q*(d.id==="watermelon"?45:25);record(s,{at:new Date(time(s.clock)-j*minute).toISOString(),dishId:d.id,plateId:`${d.id}-1`,kind:"take",quantity:q,weightG:weight});}
 });
 s.dishes.forEach(d=>{
  const entries=s.ledger.filter(l=>l.dishId===d.id);
  const total=(kind:string,field:"quantity"|"weightG")=>entries.filter(l=>l.kind===kind).reduce((n,l)=>n+l[field],0);
  const plates=Object.values(s.plates).filter(p=>p.dishId===d.id);
  const closingQ=plates.reduce((n,p)=>n+p.quantity,0),closingG=plates.reduce((n,p)=>n+p.weightG,0);
  const balanceQ=closingQ+total("take","quantity")+total("waste","quantity")-total("refill","quantity");
  const balanceG=closingG+total("take","weightG")+total("waste","weightG")-total("refill","weightG");
  s.ledger=s.ledger.filter(l=>!(l.dishId===d.id&&l.kind==="opening"));
  if(balanceQ>=0&&balanceG>=0)record(s,{at:"2026-10-03T11:00:00+08:00",dishId:d.id,plateId:"demo-opening",kind:"opening",quantity:balanceQ,weightG:balanceG});
  else record(s,{at:"2026-10-03T18:30:00+08:00",dishId:d.id,plateId:"demo-withdraw",kind:"withdraw",quantity:Math.max(0,-balanceQ),weightG:Math.max(0,-balanceG),reason:"演示记录：撤下保留"});
 });
 s.ledger.sort((a,b)=>time(a.at)-time(b.at));
 generateTasks(s);return s;
}
function validPlate(s:State,p:State["plates"][string],now:string){return !p.removed&&!p.issue&&p.quality>=.85&&time(now)-time(p.seenAt)<=s.settings.staleMinutes*minute;}
export function summarize(s:State,now=s.clock):Summary {
 const day=shanghaiDate(now); const ledger=s.ledger.filter(l=>shanghaiDate(l.at)===day);const orders=s.orders.filter(o=>shanghaiDate(o.at)===day);
 const dishes:DishSummary[]=s.dishes.map(d=>{
  const all=Object.values(s.plates).filter(p=>p.dishId===d.id&&!p.removed);
  const good=all.filter(p=>validPlate(s,p,now));const events=ledger.filter(l=>l.dishId===d.id);
  const recent=s.ledger.filter(l=>l.dishId===d.id&&l.kind==="take"&&time(l.at)>time(now)-10*minute&&time(l.at)<=time(now));
  // Zero demand uses the configured floor; missing measurements suspend automation.
  const rate=recent.reduce((v,l)=>v+l.quantity,0)/10;
  const stock=good.reduce((v,p)=>v+p.quantity,0),weightG=good.reduce((v,p)=>v+p.weightG,0);
  const take=events.filter(l=>l.kind==="take"),waste=events.filter(l=>l.kind==="waste"),refill=events.filter(l=>l.kind==="refill");
  const minutesLeft=rate>0?stock/rate:null;
  const anomaly=all.length>good.length;
  const incoming=s.tasks.filter(t=>t.dishId===d.id&&(t.status==="pending"||t.status==="preparing")).reduce((n,t)=>n+Math.max(0,t.quantity-t.actual),0);
  const status=anomaly?"anomaly":minutesLeft!==null&&minutesLeft<=d.leadMinutes+d.safetyMinutes?"urgent":stock<d.target*.4?"watch":"okay";
  return {...d,stock,weightG,plates:all.length,rate,minutesLeft,incoming,status,takeG:take.reduce((a,l)=>a+l.weightG,0),takeQuantity:take.reduce((a,l)=>a+l.quantity,0),wasteG:waste.reduce((a,l)=>a+l.weightG,0),wasteCost:waste.reduce((a,l)=>a+l.weightG*(l.costPerKg??0)/1000,0),refillCount:refill.length,refillQuantity:refill.reduce((a,l)=>a+l.quantity,0),costReady:effectiveCost(s,d)!==null&&events.every(l=>l.costPerKg!==null),expense:events.filter(l=>l.kind==="take"||l.kind==="waste").reduce((a,l)=>a+l.weightG*(l.costPerKg??0)/1000,0)};
 });
 const guests=orders.reduce((v,o)=>v+o.guests,0),revenue=orders.reduce((v,o)=>v+o.revenue,0);
 const costReady=dishes.every(d=>d.costReady);
 const foodExpense=dishes.reduce((v,d)=>v+d.expense,0),wasteCost=dishes.reduce((v,d)=>v+d.wasteCost,0);
 const hourly=Array.from({length:12},(_,i)=>{const hour=String(i+11).padStart(2,"0");return {hour:`${hour}:00`,guests:orders.filter(o=>shanghaiTime(o.at).startsWith(hour)).reduce((v,o)=>v+o.guests,0),takeG:ledger.filter(l=>l.kind==="take"&&shanghaiTime(l.at).startsWith(hour)).reduce((v,l)=>v+l.weightG,0)};});
 return {source:s.source,clock:now,dishes,tasks:[...s.tasks].sort((a,b)=>time(b.createdAt)-time(a.createdAt)),ledger:[...ledger].reverse().slice(0,200),orders,settings:s.settings,revision:s.revision,metrics:{guests,revenue,takenG:dishes.reduce((v,d)=>v+d.takeG,0),wastedG:dishes.reduce((v,d)=>v+d.wasteG,0),wasteCost,foodExpense,perGuestCost:guests&&costReady?foodExpense/guests:null,perGuestWaste:guests&&costReady?wasteCost/guests:null,foodMargin:guests&&costReady?revenue-foodExpense:null,pending:s.tasks.filter(t=>t.status==="pending"||t.status==="preparing").length,anomaly:dishes.filter(d=>d.status==="anomaly").length,costReady},hourly,integrations:{deepseekConfigured:false,ingestConfigured:false,model:"deepseek-flash"},imports:s.imports||emptyImports()};
}
export function generateTasks(s:State){
 if(!s.settings.autoEnabled)return;
 const view=summarize(s);const local=shanghaiTime(s.clock);const untilClose=(Number(s.settings.closeTime.slice(0,2))*60+Number(s.settings.closeTime.slice(3)))-(Number(local.slice(0,2))*60+Number(local.slice(3)));
 if(untilClose<=0)return;
 for(const d of view.dishes){
  if(!d.confirmed||d.status==="anomaly"||!Object.values(s.plates).some(p=>p.dishId===d.id&&!p.removed))continue;
  const active=s.tasks.find(t=>t.dishId===d.id&&(t.status==="pending"||t.status==="preparing"));
  if(active)continue;
  const recentlyCancelled=s.tasks.some(t=>t.dishId===d.id&&t.status==="cancelled"&&time(s.clock)-time(t.updatedAt)<5*minute);if(recentlyCancelled)continue;
  const threshold=Math.max(d.batch*.5,d.rate*(d.leadMinutes+d.safetyMinutes));
  if(d.stock>threshold||untilClose<=d.leadMinutes)continue;
  const horizon=Math.min(20,untilClose);const target=Math.min(d.target,Math.max(d.batch,d.rate*horizon));
  let quantity=Math.ceil(Math.max(0,target-d.stock-d.incoming)/d.batch)*d.batch;
  if(untilClose<=30)quantity=Math.min(quantity,d.batch);
  if(quantity<=0)continue;
  const deadline=time(s.clock)+Math.max(1,Math.floor((d.minutesLeft??d.leadMinutes)-d.safetyMinutes))*minute;
  s.tasks.push({id:uid(),dishId:d.id,quantity,createdAt:s.clock,dueAt:new Date(deadline).toISOString(),status:"pending",reason:`场上${d.stock}${d.unit}，近10分钟取用${d.rate.toFixed(1)}${d.unit}/分钟；准备${d.leadMinutes}分钟，安全余量${d.safetyMinutes}分钟${untilClose<=30?"；闭店前仅补一个批量":""}`,actual:0,refillBaseline:s.ledger.filter(l=>l.dishId===d.id&&l.kind==="refill").reduce((n,l)=>n+l.quantity,0),updatedAt:s.clock});
 }
}
export function applyObservation(s:State,e:Observation){
 const fingerprint=JSON.stringify(Object.fromEntries(Object.entries(e).sort(([a],[b])=>a.localeCompare(b))));
 if(s.seenEventIds.includes(e.eventId)){if(s.imports?.observations[e.eventId]&&s.imports.observations[e.eventId]!==fingerprint)throw new Error("设备事件编号已存在且内容不同");return "duplicate";}
 const d=s.dishes.find(d=>d.id===e.dishId);if(!d)throw new Error("未知菜品标识");
 if(!Number.isFinite(time(e.timestamp)))throw new Error("无效观测时间");
 if(time(e.timestamp)>time(s.clock)+5*minute)throw new Error("观测时间超出当前时间");
 if(e.refillArrivalAt&&(!Number.isFinite(time(e.refillArrivalAt))||time(e.refillArrivalAt)<time(e.timestamp)))throw new Error("预计到口时间不能早于采样时间");
 const existing=s.plates[e.plateId];
 if(existing&&time(e.timestamp)<=time(existing.seenAt))throw new Error("同盘观测时间必须递增；重复事件请使用原 eventId");
 if(existing&&!existing.removed&&existing.dishId!==e.dishId)throw new Error("换菜前请先撤盘，再以新的盘子标识上盘");
 if(existing?.removed)throw new Error("盘子已撤下，再次上盘需使用新的盘子标识");
 if(e.kind==="remove"&&(!existing||existing.removed))throw new Error("撤盘必须对应在场盘子");
 if(e.kind==="add"&&existing)throw new Error("新上盘需使用新的盘子标识；同盘补充使用 observe");
 if(d.unit==="件"&&e.count===undefined)throw new Error("计数菜品需要 count");
 const q=d.unit==="g"?e.netWeightG:e.count!;
 if(e.kind==="remove"&&!e.disposition)throw new Error("撤盘需要处置方式");
 if(e.kind==="remove"&&!e.reason?.trim())throw new Error("撤盘需要处置原因");
 const p={id:e.plateId,dishId:e.dishId,quantity:q,weightG:e.netWeightG,seenAt:e.timestamp,quality:e.confidence,refillArrivalAt:e.refillArrivalAt,removed:false,issue:undefined as string|undefined};
 if(q>d.capacity||e.confidence<.85||(q===0&&e.netWeightG>5))p.issue="重量或识别异常，待人工核查";
 if(p.issue&&e.kind==="remove")throw new Error("撤盘读数异常，请复核后提交");
 if(existing?.issue&&e.kind==="observe"){
  // Resume from the last trusted baseline; failed observations never replace it.
 }
 if(!p.issue){
  if(e.kind==="remove"){
   if(q>existing!.quantity||e.netWeightG>existing!.weightG+3)throw new Error("撤盘余量超过上次可信余量，请先提交补充观测");
   if(existing!.quantity>q)record(s,{at:e.timestamp,dishId:d.id,plateId:e.plateId,kind:"take",quantity:existing!.quantity-q,weightG:Math.max(0,existing!.weightG-e.netWeightG)});
   record(s,{at:e.timestamp,dishId:d.id,plateId:e.plateId,kind:e.disposition==="retain"?"withdraw":"waste",quantity:q,weightG:e.netWeightG,reason:e.reason});p.removed=true;
  }else if(!existing){record(s,{at:e.timestamp,dishId:d.id,plateId:e.plateId,kind:e.kind==="add"?"refill":"opening",quantity:q,weightG:e.netWeightG});}
  else {
   const delta=q-existing.quantity, mass=e.netWeightG-existing.weightG;
   if((delta>0&&mass< -3)||(delta<0&&mass>3))p.issue="计数与重量方向不一致，待复核";
   else if(Math.abs(delta)>(d.unit==="g"?3:0))record(s,{at:e.timestamp,dishId:d.id,plateId:e.plateId,kind:delta>0?"refill":"take",quantity:Math.abs(delta),weightG:Math.abs(mass),reason:delta>0?"同盘余量增加，推定补充":undefined});
   else if(d.unit==="g"){p.quantity=existing.quantity;p.weightG=existing.weightG;}
  }
 }
 if(p.issue&&existing){s.plates[e.plateId]={...existing,seenAt:e.timestamp,quality:e.confidence,issue:p.issue};}
 else s.plates[e.plateId]=p;
 s.seenEventIds.push(e.eventId);if(s.imports)s.imports.observations[e.eventId]=fingerprint;
 updateTaskCredits(s);generateTasks(s);return p.issue?"anomaly":"accepted";
}
function updateTaskCredits(s:State){for(const t of s.tasks.filter(t=>t.status==="pending"||t.status==="preparing")){t.actual=s.ledger.filter(l=>l.kind==="refill"&&l.dishId===t.dishId).reduce((n,l)=>n+l.quantity,0)-t.refillBaseline;}}
export function simulate(s:State){
 if(s.source!=="demo")throw new Error("真实数据不能运行模拟");
 s.clock=new Date(time(s.clock)+minute).toISOString();
 const tick=Math.floor(time(s.clock)/minute);
 for(const d of s.dishes){
  const active=Object.values(s.plates).filter(p=>p.dishId===d.id&&!p.removed);
  let remaining=d.unit==="g"?[70,25,50,28,17,16,30,12,16,20,15,21][s.dishes.indexOf(d)]:d.id==="shrimp"?2:1;
  for(const p of active){const taken=Math.min(p.quantity,remaining);remaining-=taken;const quantity=p.quantity-taken;const weight=p.quantity?Math.round(p.weightG*quantity/p.quantity):0;applyObservation(s,{eventId:`sim-${tick}-${p.id}`,timestamp:s.clock,plateId:p.id,dishId:d.id,kind:"observe",netWeightG:weight,count:d.unit==="件"?quantity:undefined,confidence:.98});}
 }
 generateTasks(s);
}
export function taskAction(s:State,id:string,status:Task["status"],quantity?:number,note?:string,delayMinutes?:number){
 const t=s.tasks.find(t=>t.id===id);if(!t)throw new Error("任务不存在");
 if(t.status==="completed"||t.status==="cancelled")throw new Error("任务已结束");
 const d=s.dishes.find(d=>d.id===t.dishId)!;
 if(quantity!==undefined){if(quantity<=0||quantity>20000||(d.unit==="件"&&!Number.isInteger(quantity)))throw new Error("补菜数量无效");t.quantity=quantity;}
 if(status==="cancelled"&&!note?.trim())throw new Error("取消任务请填写原因");
 if(status==="pending"&&delayMinutes){if(!note?.trim())throw new Error("延后任务请填写原因");t.availableAt=new Date(time(s.clock)+delayMinutes*minute).toISOString();t.dueAt=new Date(time(t.dueAt)+delayMinutes*minute).toISOString();}
 if(status==="preparing"&&t.availableAt&&time(t.availableAt)>time(s.clock))throw new Error("尚未到延后任务的开始时间");
 if(status==="completed"){
  if(s.source==="demo"){
   let left=Math.max(0,t.quantity-t.actual);
   while(left>0){const q=Math.min(left,d.capacity);const id=`demo-refill-${uid()}`;applyObservation(s,{eventId:uid(),timestamp:s.clock,plateId:id,dishId:d.id,kind:"add",netWeightG:d.unit==="g"?q:q*(d.id==="watermelon"?45:25),count:d.unit==="件"?q:undefined,confidence:.99});left-=q;}
  }else if(t.actual<t.quantity)throw new Error("尚未观测到足量补充，请等待称重记录或调整任务数量");
 }
 t.status=status;t.updatedAt=s.clock;t.note=note?.trim()||t.note;if(status!=="pending")delete t.availableAt;
}
export function recordWaste(s:State,plateId:string,disposition:Observation["disposition"],reason:string){
 const p=s.plates[plateId];if(!p||p.removed)throw new Error("请选择在场盘子");
 applyObservation(s,{eventId:uid(),timestamp:new Date(Math.max(time(s.clock),time(p.seenAt)+1)).toISOString(),plateId,dishId:p.dishId,kind:"remove",netWeightG:p.weightG,count:p.quantity,confidence:p.quality,disposition,reason});
}
export function ruleAnalysis(v:Summary,question:string):string {
 if(v.source!=="demo"&&!v.ledger.length&&!v.orders.length)return "当前暂无经营数据，暂不能推断需求高峰、顾客喜好或经营收益。";
 const ranked=[...v.dishes].sort((a,b)=>b.takeG-a.takeG);const waste=[...v.dishes].sort((a,b)=>b.wasteCost-a.wasteCost);const peak=[...v.hourly].sort((a,b)=>b.guests-a.guests)[0];
 const pending=v.tasks.filter(t=>t.status==="pending"||t.status==="preparing");
 if(!v.metrics.costReady&&/成本|利润/.test(question))return "真实成本尚未确认或配方成本缺失，暂不计算成本与毛利。历史缺失成本不会被默认数值替代。";
 const prefix=v.source==="test"?"以下基于联调虚拟数据，不代表真实经营。":v.source==="demo"?"以下基于模拟数据，是规则分析演示。":"以下基于已接入记录，是规则分析。";
 const body=/补|缺|库存/.test(question)?`当前有 ${pending.length} 个未结束补菜任务。${pending.slice(0,3).map(t=>`${v.dishes.find(d=>d.id===t.dishId)?.name}：建议 ${t.quantity}${v.dishes.find(d=>d.id===t.dishId)?.unit}，依据为${t.reason}`).join("；")}。请结合实际后厨准备时间执行。`:/浪费|报损|成本|利润/.test(question)?`当日撤盘报损 ${(v.metrics.wastedG/1000).toFixed(2)} kg，估算报损成本 ¥${v.metrics.wasteCost.toFixed(2)}。报损成本较高的菜品为 ${waste.slice(0,3).map(d=>`${d.name} ¥${d.wasteCost.toFixed(2)}`).join("、")}。建议试验小批量补充，并观察缺菜和满意度是否变化。`:/画像|客群|高峰|人数/.test(question)?`订单记录中的到店人数高峰为 ${peak.hour} 时段，共 ${peak.guests} 人。${v.metrics.guests?`今日已记录 ${v.metrics.guests} 位顾客。`:"尚无收银人数记录。"}只能描述时段群体的取用结构，不能据此判断个人年龄、身份或个人喜好；在店高峰还需要入座与离店记录。`:`当前取用重量排名前列为 ${ranked.slice(0,3).map(d=>`${d.name} ${(d.takeG/1000).toFixed(2)}kg`).join("、")}。今日取用 ${(v.metrics.takenG/1000).toFixed(2)}kg，撤盘报损 ${(v.metrics.wastedG/1000).toFixed(2)}kg。建议先关注未结束的 ${pending.length} 个补菜任务，再核查报损高的菜品。`;
 return `${prefix}\n\n${body}\n\n取用量不等于吃掉的量或真实喜好；食材毛利不等于净利润，未扣人工、租金、能源和其他成本。建议效果需要现场试验验证。`;
}
