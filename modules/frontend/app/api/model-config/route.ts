import { z } from "zod";
import { ModelConfigurationError, keyStatus, deepseekKey, verifyDeepseekKey, saveDeepseekKey, removeDeepseekKey } from "@/lib/deepseek";
import { checkOrigin, body } from "@/lib/http";
const headers={"Cache-Control":"no-store"};
function error(e:unknown) {
  // Never echo provider bodies, request payloads, or submitted key values.
  return Response.json({error:e instanceof z.ZodError?"请填写有效的 API Key":e instanceof SyntaxError?"输入格式不正确":e instanceof ModelConfigurationError?e.message:"配置失败，请稍后重试"},{status:400,headers});
}
function protect(req:Request) {
  // This shared configuration is behind the Site's owner-private dispatch boundary.
  try { checkOrigin(req); } catch { throw new ModelConfigurationError("请求来源不匹配"); }
  if(req.headers.get("sec-fetch-site")==="cross-site") throw new ModelConfigurationError("请求来源不匹配");
  if(req.method==="POST" && !req.headers.get("content-type")?.startsWith("application/json")) throw new ModelConfigurationError("输入格式不正确");
}
export async function GET() { try { return Response.json(await keyStatus(),{headers}); } catch { return Response.json({error:"无法读取模型配置，请重试"},{status:503,headers}); } }
export async function POST(req:Request) {
  try {
    protect(req);
    const input=z.discriminatedUnion("action",[
      z.object({action:z.literal("save"),apiKey:z.string().trim().min(16).max(256).regex(/^[A-Za-z0-9_.-]+$/)}).strict(),
      z.object({action:z.literal("test")}).strict()
    ]).parse(await body(req));
    if(input.action==="save") {
      if(!(await keyStatus()).storageReady) throw new ModelConfigurationError("密钥保存服务未就绪");
      await verifyDeepseekKey(input.apiKey);
      await saveDeepseekKey(input.apiKey);
    } else { const key=await deepseekKey(); if(!key) throw new ModelConfigurationError("请先保存 API Key"); await verifyDeepseekKey(key); }
    return Response.json({...(await keyStatus()),message:input.action==="save"?"验证成功，API Key 已保存":"连接成功，当前分析模型可用"},{headers});
  } catch(e) { return error(e); }
}
export async function DELETE(req:Request) { try { protect(req); await removeDeepseekKey(); return Response.json({...await keyStatus(),message:"已移除密钥，分析助手将使用规则分析"},{headers}); } catch(e) { return error(e); } }
