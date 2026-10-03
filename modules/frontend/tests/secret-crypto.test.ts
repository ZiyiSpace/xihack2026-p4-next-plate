import { test } from "node:test";
import assert from "node:assert/strict";
import { encryptSecret, decryptSecret } from "../lib/secret-crypto.ts";
const secret=Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString("base64");
test("keys round-trip with randomized ciphertext and no plaintext",async()=>{
 const value="sk-koala-credential-for-testing-only";
 const one=await encryptSecret(value,secret),two=await encryptSecret(value,secret);
 assert.notEqual(one,two);assert.ok(!one.includes(value));assert.equal(await decryptSecret(one,secret),value);
});
test("tampering and wrong encryption roots cannot decrypt",async()=>{
 const encrypted=await encryptSecret("sk-koala-test-credential",secret);
 const parts=encrypted.split(".");const bytes=Buffer.from(parts[2],"base64");bytes[0]^=1;parts[2]=bytes.toString("base64");
 await assert.rejects(decryptSecret(parts.join("."),secret));
 await assert.rejects(decryptSecret(encrypted,Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString("base64")));
});
test("invalid format and encryption root are rejected",async()=>{
 await assert.rejects(decryptSecret("bad-record",secret));
 await assert.rejects(encryptSecret("key",Buffer.from("short").toString("base64")));
});
