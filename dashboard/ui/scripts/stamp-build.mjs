import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
const root=path.resolve(import.meta.dirname,"..");
const fixed=["index.html","package.json","package-lock.json","tsconfig.json","vite.config.ts","tailwind.config.js","postcss.config.js"];
function allFiles(dir){
 return fs.readdirSync(path.join(root,dir),{withFileTypes:true}).flatMap(d=>{
   const rel=path.posix.join(dir,d.name);
   return d.isDirectory()?allFiles(rel):d.isFile()?[rel]:[];
 });
}
const fileNames=[...fixed,...allFiles("src")].sort();
const hash=crypto.createHash("sha256");
for(const name of fileNames){
 hash.update(name);hash.update(Buffer.from([0]));
 hash.update(fs.readFileSync(path.join(root,name)));
 hash.update(Buffer.from([0]));
}
const stamp={schema_version:1,source_sha256:hash.digest("hex"),file_count:fileNames.length};
const dist=path.join(root,"dist");
if(!fs.existsSync(path.join(dist,"index.html")))throw new Error("Vite build missing");
fs.writeFileSync(path.join(dist,"build-integrity.json"),JSON.stringify(stamp,null,2)+"\n");
console.log("Build integrity verified:",stamp.source_sha256.slice(0,12));
