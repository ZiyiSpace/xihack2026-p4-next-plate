import { z } from "zod";
export const sourceSchema=z.enum(["demo","live","test"]);
export { observationSchema } from "./observation-schema.ts";
export function failure(e:unknown,status=400){if(e instanceof z.ZodError)return Response.json({error:"输入格式不正确",details:e.issues.map(i=>`${i.path.join(".")}: ${i.message}`)},{status:400});console.error(e instanceof Error?e.message:"request failed");return Response.json({error:e instanceof Error?e.message:"操作失败，请重试"},{status});}
export function checkOrigin(req:Request){const origin=req.headers.get("origin");if(origin&&origin!==new URL(req.url).origin)throw new Error("请求来源不匹配");}
export async function body(req:Request){const raw=await req.text();if(raw.length>100000)throw new Error("请求过大");return JSON.parse(raw);}
