// Runs only against local development with a fake provider; no real API keys or paid calls.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
const root=new URL("../",import.meta.url);
const envFile=new URL(".env",root);
const original=await readFile(envFile,"utf8");
let providerMode="ok",authSeen="";
const key="sk-koala-smoke-test-credential-1234";
const mock=createServer(async(req,res)=>{
 authSeen=req.headers.authorization||"";
 res.setHeader("Content-Type","application/json");
 if(providerMode==="redirect"){res.statusCode=302;res.setHeader("Location","http://unrelated.invalid/models");res.end("{}");return;}
 if(providerMode==="bad"){res.statusCode=401;res.end(JSON.stringify({error:{message:`unsafe reflection ${authSeen}`}}));return;}
 if(req.url==="/models")res.end(JSON.stringify({data:[{id:"deepseek-flash"}]}));
 else if(req.url==="/chat/completions")res.end(JSON.stringify({choices:[{message:{content:"本地模拟模型回答"}}]}));
 else {res.statusCode=404;res.end("{}");}
});
await new Promise(resolve=>mock.listen(0,"127.0.0.1",resolve));
const provider=`http://127.0.0.1:${mock.address().port}`;
const origin="http://127.0.0.1:5173";
let child,output="";
const call=async(method,payload,requestOrigin=origin)=>{
 const response=await fetch(`${origin}/api/model-config`,{method,headers:{"Content-Type":"application/json",Origin:requestOrigin},body:payload?JSON.stringify(payload):undefined});
 const text=await response.text();assert.ok(!text.includes(key));assert.ok(!text.includes("unsafe reflection"));
 return {status:response.status,body:text.startsWith("{")?JSON.parse(text):{error:text},cache:response.headers.get("cache-control")};
};
try {
 let local=original.replace(/^DEEPSEEK_BASE_URL=.*$/m,`DEEPSEEK_BASE_URL=${provider}`).replace(/^DEEPSEEK_API_KEY=.*$/m,"DEEPSEEK_API_KEY=sk-koala-env-fallback-for-test");
 await writeFile(envFile,local);
 child=spawn(process.execPath,["scripts/run-framework.mjs","dev"],{cwd:root,stdio:["ignore","pipe","pipe"]});
 child.stdout.on("data",data=>{output+=data;});child.stderr.on("data",data=>{output+=data;});
 let ready=false;
 for(let i=0;i<60;i++){try{const r=await fetch(`${origin}/api/model-config`);if(r.ok){ready=true;break;}}catch{} await new Promise(resolve=>setTimeout(resolve,500));}
 assert.ok(ready,"local dev did not become ready");
 // Disabling must suppress even a pre-existing environment fallback.
 assert.equal((await call("DELETE")).body.configured,false);
 assert.equal((await call("GET")).cache,"no-store");
 assert.equal((await call("POST",{action:"test"})).status,400);
 assert.equal((await call("POST",{action:"save",apiKey:"bad"})).status,400);
 assert.ok([400,403].includes((await call("POST",{action:"save",apiKey:key},"https://unrelated.invalid")).status));
 const saved=await call("POST",{action:"save",apiKey:key});assert.equal(saved.status,200,JSON.stringify(saved.body));assert.equal(saved.body.configured,true);assert.match(saved.body.maskedKey,/1234$/);assert.equal(authSeen,`Bearer ${key}`);
 assert.equal((await call("POST",{action:"test"})).status,200);
 const state=await fetch(`${origin}/api/state?source=demo`);const snapshot=await state.text();assert.ok(!snapshot.includes(key));assert.equal(JSON.parse(snapshot).integrations.deepseekConfigured,true);
 const analysis=await fetch(`${origin}/api/analysis`,{method:"POST",headers:{"Content-Type":"application/json",Origin:origin},body:JSON.stringify({source:"demo",question:"补菜建议"})});assert.equal((await analysis.json()).provider,"deepseek");assert.equal(authSeen,`Bearer ${key}`);
 providerMode="redirect";assert.equal((await call("POST",{action:"test"})).status,400);
 providerMode="bad";
 assert.equal((await call("POST",{action:"save",apiKey:"sk-wrong-replacement-test-9999"})).status,400);
 assert.match((await call("GET")).body.maskedKey,/1234$/);
 const failedTest=await call("POST",{action:"test"});assert.equal(failedTest.status,400);assert.match(failedTest.body.error,/无效/);
 assert.ok([400,403].includes((await call("DELETE",undefined,"https://unrelated.invalid")).status));
 assert.equal((await call("GET")).body.configured,true);
 assert.equal((await call("DELETE")).body.configured,false);
 assert.equal((await call("GET")).body.maskedKey,null);
 const fallback=await fetch(`${origin}/api/analysis`,{method:"POST",headers:{"Content-Type":"application/json",Origin:origin},body:JSON.stringify({source:"demo",question:"补菜建议"})});assert.equal((await fallback.json()).provider,"rules");
 console.log("PASS: local model configuration — save/test, masked responses, shared analysis use, failed replacement preserves key, cross-origin rejection, removal suppresses environment fallback.");
} finally {
 if(child&&child.exitCode===null){child.kill("SIGINT");await new Promise(resolve=>child.once("exit",resolve));}
 await writeFile(envFile,original);
 await new Promise(resolve=>mock.close(resolve));
}
