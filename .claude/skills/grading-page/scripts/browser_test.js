const {chromium}=require('playwright');
(async()=>{const b=await chromium.launch({args:['--allow-file-access-from-files']});const ctx=await b.newContext({viewport:{width:1440,height:1000}});const p=await ctx.newPage();
const errs=[];p.on('pageerror',e=>errs.push('PE '+e.message));p.on('console',m=>{if(m.type()==='error')errs.push('C '+m.text().slice(0,160))});
await p.addInitScript(()=>{const store={};const subs=[];window.__store=store;
 const doc=id=>({id,data:()=>store[id]});const fire=()=>subs.forEach(f=>f({docs:Object.keys(store).map(doc)}));
 window.claude={use:async n=>n==='db'?{doc:path=>({set:async d=>{store[path.split('/')[1]]=d;fire()}}),collection:()=>({onSnapshot:(f)=>{subs.push(f);f({docs:[]});return()=>{}}})}:null}});
await p.goto('file:///w/test.html');await p.waitForTimeout(800);
await p.screenshot({path:'s-start.png'});
await p.click('button.navb:has-text("n03-beautyblender")');await p.waitForTimeout(1500);
await p.screenshot({path:'s-ad.png'});
await p.evaluate(()=>document.getElementById('s3').scrollIntoView());await p.waitForTimeout(2000);await p.screenshot({path:'s-scene.png'});
const stats=await p.evaluate(async()=>{const v=document.querySelectorAll('#s3 video');const x=v[0];x.muted=true;let r='';try{await x.play();await new Promise(r=>setTimeout(r,1200));r='playing t='+x.currentTime+' dur='+x.duration+' w='+x.videoWidth}catch(e){r='ERR '+e.message+' canH264='+x.canPlayType('video/mp4; codecs="avc1.42E01E"')}const imgs=[...document.querySelectorAll('#s3 img')];imgs.forEach(i=>i.loading='eager');await new Promise(r=>setTimeout(r,2500));return{r,videos:v.length,imgs:imgs.length,broken:imgs.filter(i=>i.complete&&!i.naturalWidth).length,nofile:document.querySelectorAll('.nofile').length,overflow:document.documentElement.scrollWidth>innerWidth}});console.log(stats);
const ta=await p.$('#s3 textarea');await ta.fill('test words');await p.waitForTimeout(1200);console.log('stored',await p.evaluate(()=>JSON.stringify(window.__store)));
const bt=await p.$('#s3 .grade button[data-v=fail]');await bt.click();await p.waitForTimeout(300);console.log('stored',await p.evaluate(()=>JSON.stringify(window.__store)),await p.textContent('#s3 .st'));
for(const k of ['n07-gymreapers-straps','n05-dreamfarm-garject','n11-chipolo-pop']){await p.click('button.navb:has-text("'+k+'")');await p.waitForTimeout(300)}
await p.setViewportSize({width:400,height:800});await p.waitForTimeout(500);await p.evaluate(()=>scrollTo(0,0));await p.screenshot({path:'s-phone.png'});console.log('phone overflow',await p.evaluate(()=>document.documentElement.scrollWidth));
console.log(errs.slice(0,8));await b.close()})().catch(e=>{console.error(e);process.exit(1)});
