import { env } from "cloudflare:workers";
import { z } from "zod";
import { observationSchema,body,failure } from "@/lib/http";
import { mutateState } from "@/lib/store";
import { applyObservation } from "@/lib/engine";
async function equal(a:string,b:string){const encode=new TextEncoder();const hashes=await Promise.all([crypto.subtle.digest("SHA-256",encode.encode(a)),crypto.subtle.digest("SHA-256",encode.encode(b))]);const x=new Uint8Array(hashes[0]),y=new Uint8Array(hashes[1]);let diff=0;for(let i=0;i<x.length;i++)diff|=x[i]^y[i];return diff===0;}
export async function POST(req:Request){try{if(!env.INGEST_API_KEY)return Response.json({error:"采集接口尚未配置"},{status:503});if(!await equal(req.headers.get("authorization")||"",`Bearer ${env.INGEST_API_KEY}`))return Response.json({error:"采集认证失败"},{status:401});const input=z.object({events:z.array(observationSchema).min(1).max(100)}).strict().parse(await body(req));const r=await mutateState("live",s=>input.events.map(e=>({eventId:e.eventId,status:applyObservation(s,e)})));return Response.json({success:true,results:r.result,revision:r.state.revision});}catch(e){return failure(e)}}
