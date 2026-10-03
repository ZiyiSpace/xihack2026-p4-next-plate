import { env } from "cloudflare:workers";
import { encryptSecret, decryptSecret } from "./secret-crypto";
export class ModelConfigurationError extends Error {}
interface KeyRow { ciphertext: string|null; suffix:string|null; disabled:number; updated_at:string; }
export interface KeyStatus { configured:boolean; maskedKey:string|null; model:string; updatedAt:string|null; storageReady:boolean; }
function database() { if(!env.DB) throw new ModelConfigurationError("密钥保存服务暂不可用"); return env.DB; }
async function row() { return database().prepare("SELECT ciphertext, suffix, disabled, updated_at FROM model_credentials WHERE id = 'deepseek'").first<KeyRow>(); }
export function deepseekModel() { return env.DEEPSEEK_MODEL || "deepseek-flash"; }
export function deepseekUrl(path:string) { return `${(env.DEEPSEEK_BASE_URL || "https://api.deepseek.com").replace(/\/$/, "")}/${path}`; }
export async function keyStatus():Promise<KeyStatus> {
  const saved = await row();
  const configured = saved ? !saved.disabled && !!saved.ciphertext : !!env.DEEPSEEK_API_KEY;
  return { configured, maskedKey:configured?`••••••••${saved?.suffix || env.DEEPSEEK_API_KEY?.slice(-4) || ""}`:null, model:deepseekModel(), updatedAt:saved?.updated_at || null, storageReady:!!env.MODEL_KEY_ENCRYPTION_KEY };
}
export async function deepseekKey() {
  const saved = await row();
  if (!saved) return env.DEEPSEEK_API_KEY || null;
  if (saved.disabled || !saved.ciphertext) return null;
  if (!env.MODEL_KEY_ENCRYPTION_KEY) throw new ModelConfigurationError("密钥保存服务未就绪");
  try { return await decryptSecret(saved.ciphertext, env.MODEL_KEY_ENCRYPTION_KEY); }
  catch { throw new ModelConfigurationError("无法读取已保存的密钥，请重新配置"); }
}
export async function saveDeepseekKey(key:string) {
  if (!env.MODEL_KEY_ENCRYPTION_KEY) throw new ModelConfigurationError("密钥保存服务未就绪");
  const encrypted = await encryptSecret(key, env.MODEL_KEY_ENCRYPTION_KEY);
  await database().prepare("INSERT INTO model_credentials (id, ciphertext, suffix, disabled, updated_at) VALUES ('deepseek', ?, ?, 0, ?) ON CONFLICT(id) DO UPDATE SET ciphertext=excluded.ciphertext, suffix=excluded.suffix, disabled=0, updated_at=excluded.updated_at").bind(encrypted,key.slice(-4),new Date().toISOString()).run();
}
export async function removeDeepseekKey() {
  // A tombstone prevents an environment fallback from unexpectedly enabling the model again.
  await database().prepare("INSERT INTO model_credentials (id, ciphertext, suffix, disabled, updated_at) VALUES ('deepseek', NULL, NULL, 1, ?) ON CONFLICT(id) DO UPDATE SET ciphertext=NULL, suffix=NULL, disabled=1, updated_at=excluded.updated_at").bind(new Date().toISOString()).run();
}
export async function verifyDeepseekKey(key:string) {
  let response:Response;
  try { response=await fetch(deepseekUrl("models"), {headers:{Authorization:`Bearer ${key}`}, redirect:"manual", signal:AbortSignal.timeout(12000)}); }
  catch { throw new ModelConfigurationError("连接超时或网络不可用，请稍后重试"); }
  if(response.status===401 || response.status===403) throw new ModelConfigurationError("API Key 无效或无访问权限，请检查后重试");
  if(response.status===402) throw new ModelConfigurationError("账号余额不足，请充值后重试");
  if(response.status===429) throw new ModelConfigurationError("请求过于频繁，请稍后重试");
  if(!response.ok) throw new ModelConfigurationError("DeepSeek 服务暂时不可用，请稍后重试");
  const data=await response.json() as {data?:{id?:string}[]};
  if(!Array.isArray(data.data) || !data.data.some(model=>model.id===deepseekModel())) throw new ModelConfigurationError("密钥可连接，但当前分析模型不可用，请联系维护人员");
}
