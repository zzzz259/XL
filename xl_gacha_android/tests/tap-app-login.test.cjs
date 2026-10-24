const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/tap-app-login.js'),'utf8');
const QR='https://accounts.taptap.cn/device?qrcode=qaChallenge&user_code=QA123456';
function page(origin='https://accounts.taptap.cn',path='/login'){
 let now=1000,modeClicks=0,qr=false;
 const calls=[];
 const icon={click(){modeClicks++;qr=!qr;}};
 const document={querySelector(selector){return selector==='.qrcode-login'?(qr?{}:null):selector==='.login-page__content__login-mode-icon'?icon:null;}};
 const fetch=async url=>{calls.push(url);return {ok:true,clone(){return {json:async()=>({data:{qrcode_url:QR,expires_in:120}})}}};};
 const window={fetch,location:{origin,pathname:path,href:origin+path}};
 class XHR {open(method,url){this.url=url;}addEventListener(name,fn){this.listener=fn;}send(){this.status=200;this.responseType='';this.responseText=JSON.stringify({data:{qrcode_url:QR}});this.listener?.();}}
 window.XMLHttpRequest=XHR;
 const ctx=vm.createContext({window,location:window.location,document,URL,Date:{now:()=>now},setTimeout:fn=>fn(),XMLHttpRequest:XHR});
 vm.runInContext(source,ctx);
 return {window,calls,ctx,advance(ms){now+=ms;},clicks:()=>modeClicks,helper:()=>window.__starfallTapApp};
}
test('captures only the official device-code response and preserves its response',async()=>{
 const p=page();assert.equal(p.helper().read().phase,'waiting');
 await p.window.fetch('/api/other');await Promise.resolve();assert.equal(p.helper().read().phase,'waiting');
 const response=await p.window.fetch('/api/device-code/get');await Promise.resolve();await Promise.resolve();
 assert.equal(response.ok,true);assert.equal(p.helper().read().qrcode_url,QR);
 assert.deepEqual(p.calls,['/api/other','/api/device-code/get']);
});
test('does not hook other origins or non-login pages',()=>{
 for(const [origin,path] of [['https://evil.example','/login'],['https://accounts.taptap.cn.evil.example','/login'],['https://accounts.taptap.cn','/device'],['https://accounts.taptap.cn','/authorize']])assert.equal(page(origin,path).helper(),undefined);
});
test('prepares QR mode once, survives repeat injection, and forgets expired challenge',async()=>{
 const p=page();p.helper().request();p.helper().request();assert.equal(p.clicks(),1);
 await p.window.fetch('/api/device-code/get');await Promise.resolve();await Promise.resolve();
 vm.runInContext(source,p.ctx);assert.equal(p.helper().read().qrcode_url,QR);
 p.advance(110001);assert.equal(p.helper().read().phase,'expired');assert.equal(p.helper().read().qrcode_url,undefined);
});
test('XHR response capture is limited to the same official endpoint',()=>{
 const p=page(),x=new p.window.XMLHttpRequest();x.open('POST','https://evil.example/api/device-code/get');x.send();assert.equal(p.helper().read().phase,'waiting');
 const y=new p.window.XMLHttpRequest();y.open('POST','/api/device-code/get');y.send();assert.equal(p.helper().read().qrcode_url,QR);
});
