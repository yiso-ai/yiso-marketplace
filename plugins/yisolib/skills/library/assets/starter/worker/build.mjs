import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.dirname(fileURLToPath(import.meta.url));
const assets={};for(const [url,file,mime] of [['/','index.html','text/html'],['/app.js','app.js','text/javascript'],['/style.css','style.css','text/css']])assets[url]={body:await readFile(path.join(root,'..',file),'utf8'),mime};
let source=await readFile(path.join(root,'index.mjs'),'utf8');source=source.replace('export default {async fetch','const adapter = {async fetch');source+='\nconst embeddedAssets='+JSON.stringify(assets)+';\nexport default {fetch(request, env) {return adapter.fetch(request, {...env, ASSETS: {fetch: async req => {const asset=embeddedAssets[new URL(req.url).pathname];return asset?new Response(asset.body,{headers:{"Content-Type":asset.mime+"; charset=utf-8"}}):new Response("Not found",{status:404});}}});}};\n';
await mkdir(path.join(root,'dist/server'),{recursive:true});await writeFile(path.join(root,'dist/server/index.js'),source);console.log('Built dist/server/index.js with embedded UI assets');
