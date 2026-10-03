import test from "node:test";
import assert from "node:assert/strict";
import { nextArrival, arrivalText } from "../lib/supply.ts";
import { applyObservation, createState } from "../lib/engine.ts";
import { observationSchema } from "../lib/http.ts";
import type { Plate } from "../lib/domain.ts";
const now = Date.parse("2026-10-03T18:42:00+08:00");
const plate = (id: string, seconds: number): Plate => ({id, dishId:"beef", quantity:100, weightG:100, seenAt:new Date(now).toISOString(), quality:.98, removed:false, refillArrivalAt:new Date(now+seconds*1000).toISOString()});
test("arrival is the closest reliable plate, independent of task deadline",()=>{
 assert.equal(nextArrival([plate("later",90),plate("next",35)],"beef","live",now,5)?.plateId,"next");
 assert.equal(nextArrival([plate("next",35)],"beef","live",now+5000,5)?.seconds,30);
 assert.equal(arrivalText(65),"1分05秒");
});
test("missing, removed, suspect, stale and passed predictions do not fabricate countdowns",()=>{
 const p=plate("p",60);
 for(const invalid of [{...p,refillArrivalAt:undefined},{...p,removed:true},{...p,issue:"异常"},{...p,quality:.5},{...p,seenAt:new Date(now-6*60000).toISOString()},plate("passed",-11)])assert.equal(nextArrival([invalid],"beef","live",now,5),null);
 assert.equal(nextArrival([plate("p",0)],"beef","live",now,5)?.seconds,0);
 assert.equal(arrivalText(0),"预计已到口");
});
test("demo predictions are explicitly simulated and live does not get synthetic data",()=>{
 const p={...plate("p",60),refillArrivalAt:undefined};
 assert.equal(nextArrival([p],"beef","demo",now,5)?.simulated,true);
 assert.equal(nextArrival([p],"beef","live",now,5),null);
});
test("ingestion validates and replaces arrival prediction without changing stock",()=>{
 const s=createState("live");s.clock=new Date(now).toISOString();
 const e={eventId:"a",timestamp:new Date(now-1000).toISOString(),plateId:"p",dishId:"beef",kind:"observe" as const,netWeightG:100,confidence:.98,refillArrivalAt:new Date(now+60000).toISOString()};
 applyObservation(s,observationSchema.parse(e));assert.equal(s.plates.p.refillArrivalAt,e.refillArrivalAt);
 assert.throws(()=>applyObservation(s,{...e,eventId:"bad",timestamp:new Date(now).toISOString(),refillArrivalAt:new Date(now-1000).toISOString()}),/到口时间/);
 applyObservation(s,{...e,eventId:"b",timestamp:new Date(now).toISOString(),refillArrivalAt:undefined});
 assert.equal(s.plates.p.refillArrivalAt,undefined);assert.equal(s.plates.p.weightG,100);
});
