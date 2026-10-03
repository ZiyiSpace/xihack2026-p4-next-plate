import { env } from "cloudflare:workers";
import { keyStatus } from "@/lib/deepseek";
import { readState } from "@/lib/store";
import { summarize } from "@/lib/engine";
import { failure,sourceSchema } from "@/lib/http";
export async function GET(req:Request){try{const source=sourceSchema.parse(new URL(req.url).searchParams.get("source")||"demo");const s=await readState(source);const view=summarize(s,source!=="demo"?new Date().toISOString():s.clock);view.integrations={deepseekConfigured:(await keyStatus()).configured,ingestConfigured:!!env.INGEST_API_KEY,model:env.DEEPSEEK_MODEL||"deepseek-flash"};return Response.json({...view,plates:Object.values(s.plates).filter(p=>!p.removed)},{headers:{"Cache-Control":"no-store"}});}catch(e){return failure(e,503)}}
