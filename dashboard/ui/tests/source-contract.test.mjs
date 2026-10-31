import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
const root=path.resolve(import.meta.dirname,"..");
const fixed=["index.html","package.json","package-lock.json","tsconfig.json","vite.config.ts","tailwind.config.js","postcss.config.js"];
function allFiles(dir){
 return fs.readdirSync(path.join(root,dir),{withFileTypes:true}).flatMap(e=>{
   const file=path.posix.join(dir,e.name);
   return e.isDirectory()?allFiles(file):e.isFile()?[file]:[];
 });
}
test("a bundle exists and includes a self-hosted JS module",()=>{
 const html=fs.readFileSync(path.join(root,"dist/index.html"),"utf8");
 assert.match(html,/<div id="root">/);
 assert.match(html,/assets\/index-[A-Za-z\d_-]+\.js/);
});
test("build fingerprint exactly binds package lock and React source",()=>{
 const manifest=JSON.parse(fs.readFileSync(path.join(root,"dist/build-integrity.json"),"utf8"));
 const hash=createHash("sha256");
 const names=[...fixed,...allFiles("src")].sort();
 for(const name of names){
  hash.update(name);
  hash.update(Buffer.from([0]));
  hash.update(fs.readFileSync(path.join(root,name)));
  hash.update(Buffer.from([0]));
 }
 assert.equal(manifest.schema_version,1);
 assert.equal(manifest.file_count,names.length);
 assert.equal(manifest.source_sha256,hash.digest("hex"));
});
test("client renders verified graph and reuses owner-gated endpoints",()=>{
 const file=fs.readFileSync(path.join(root,"src/ui.tsx"),"utf8");
 assert.match(file,/api\/graph\?limit=110/);
 assert.match(file,/api\/stream\/ticks/);
 assert.match(file,/X-Forex-AI-Action/);
 assert.match(file,/Before\/after baseline is not yet instrumented/);
});
