const encoder = new TextEncoder();
const context = encoder.encode("red-koala:deepseek-api-key:v1");
function encode(bytes: Uint8Array) { return btoa(String.fromCharCode(...bytes)); }
function decode(value: string) { return Uint8Array.from(atob(value), c => c.charCodeAt(0)); }
async function encryptionKey(secret: string) {
  const bytes = decode(secret);
  if (bytes.length !== 32) throw new Error("密钥保存服务未就绪");
  return crypto.subtle.importKey("raw", bytes, "AES-GCM", false, ["encrypt", "decrypt"]);
}
export async function encryptSecret(value: string, secret: string) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: context }, await encryptionKey(secret), encoder.encode(value));
  return `v1.${encode(iv)}.${encode(new Uint8Array(encrypted))}`;
}
export async function decryptSecret(value: string, secret: string) {
  const [version, iv, encrypted, extra] = value.split(".");
  if (version !== "v1" || !iv || !encrypted || extra) throw new Error("密钥保存记录无效");
  const bytes = await crypto.subtle.decrypt({ name: "AES-GCM", iv: decode(iv), additionalData: context }, await encryptionKey(secret), decode(encrypted));
  return new TextDecoder().decode(bytes);
}
