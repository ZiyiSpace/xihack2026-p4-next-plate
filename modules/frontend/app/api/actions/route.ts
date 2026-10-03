import { z } from "zod";
import { readState,mutateState } from "@/lib/store";
import { generateTasks,simulate,taskAction,recordWaste,summarize } from "@/lib/engine";
import { body,checkOrigin,failure,sourceSchema } from "@/lib/http";
const actions=z.discriminatedUnion("action",[
 z.object({action:z.literal("simulate")}),
 z.object({action:z.literal("task"),id:z.string(),status:z.enum(["pending","preparing","completed","cancelled"]),quantity:z.number().finite().positive().max(20000).optional(),note:z.string().max(200).optional(),delayMinutes:z.number().int().min(1).max(120).optional()}),
 z.object({action:z.literal("waste"),plateId:z.string(),disposition:z.enum(["discard","other_loss","retain"]),reason:z.string().min(1).max(200)}),
 z.object({action:z.literal("settings"),autoEnabled:z.boolean(),closeTime:z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),staleMinutes:z.number().int().min(2).max(30)}),
 z.object({action:z.literal("dish"),id:z.string(),capacity:z.number().finite().positive().max(2500).optional(),batch:z.number().finite().positive().max(20000),target:z.number().finite().positive().max(50000),leadMinutes:z.number().int().min(1).max(120),safetyMinutes:z.number().int().min(1).max(30),costPerKg:z.number().finite().min(0).max(2000),confirmed:z.boolean(),costSource:z.enum(["manual","recipe"]).optional()})
]);
export async function POST(req:Request){try{checkOrigin(req);const raw=await body(req);const {source,actionId}=z.object({source:sourceSchema,actionId:z.string().uuid()}).parse(raw);const input=actions.parse(raw);const r=await mutateState(source,s=>{
 switch(input.action){
 case "simulate":simulate(s);break;
 case "task":taskAction(s,input.id,input.status,input.quantity,input.note,input.delayMinutes);break;
 case "waste":if(source!=="demo")throw new Error("真实撤盘必须通过带有称重读数的采集接口提交");recordWaste(s,input.plateId,input.disposition,input.reason);break;
 case "settings":s.settings={autoEnabled:input.autoEnabled,closeTime:input.closeTime,staleMinutes:input.staleMinutes};generateTasks(s);break;
 case "dish":{const d=s.dishes.find(d=>d.id===input.id);if(!d)throw new Error("菜品不存在");if(d.unit==="件"&&(!Number.isInteger(input.batch)||!Number.isInteger(input.target)||(input.capacity!==undefined&&(!Number.isInteger(input.capacity)||input.capacity>100))))throw new Error("件数必须是整数");Object.assign(d,{capacity:input.capacity??d.capacity,batch:input.batch,target:input.target,leadMinutes:input.leadMinutes,safetyMinutes:input.safetyMinutes,costPerKg:input.costPerKg,confirmed:input.confirmed,costSource:input.costSource??d.costSource??"manual"});generateTasks(s);break;}
 }
 },actionId);return Response.json({success:true,revision:r.state.revision,duplicate:r.duplicate});}catch(e){return failure(e)}}
