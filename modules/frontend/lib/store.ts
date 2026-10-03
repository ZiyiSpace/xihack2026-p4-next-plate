import { normalizeState } from "./cost";
import { env } from "cloudflare:workers";
import { createState } from "./engine";
import type { Source, State } from "./domain";
function db(){if(!env.DB)throw new Error("数据服务暂不可用，请稍后重试");return env.DB;}
export async function readState(source:Source){
 const binding=db();let row=await binding.prepare("SELECT payload, revision FROM store WHERE id = ?").bind(source).first<{payload:string;revision:number}>();
 if(!row){const initial=createState(source);await binding.prepare("INSERT OR IGNORE INTO store (id, revision, payload) VALUES (?, 0, ?)").bind(source,JSON.stringify(initial)).run();row=await binding.prepare("SELECT payload, revision FROM store WHERE id = ?").bind(source).first<{payload:string;revision:number}>();}
 if(!row)throw new Error("无法读取门店记录");const state=JSON.parse(row.payload) as State;state.revision=row.revision;return normalizeState(state);
}
export async function mutateState<T>(source:Source,fn:(s:State)=>T,actionId?:string){
 for(let attempt=0;attempt<4;attempt++){
  const state=await readState(source);
  if(actionId&&state.actionIds.includes(actionId))return {state,result:null,duplicate:true};
  if(source!=="demo")state.clock=new Date().toISOString();
  const result=fn(state);if(actionId)state.actionIds.push(actionId);
  const old=state.revision;state.revision++;
  const payload=JSON.stringify(state);if(new TextEncoder().encode(payload).length>1800000)throw new Error("首版记录容量已达上限，请导出并联系维护人员归档");
  const receipt=await db().prepare("UPDATE store SET payload = ?, revision = ? WHERE id = ? AND revision = ?").bind(payload,state.revision,source,old).run();
  if(receipt.meta.changes===1)return {state,result,duplicate:false};
 }
 throw new Error("记录正在被更新，请重试");
}
