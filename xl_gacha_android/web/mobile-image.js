'use strict';

const MobileImage=(()=>{
 let report=null,category='all',files=[],selected=new Set(),urls=[],generation=0,generating=false,delivering=false,notify=()=>{};
 const el=MobileRender.el;
 function update(){
  $('image-selected').textContent=`已选 ${selected.size}/${files.length} 张`;
  for(const id of ['image-save','image-share'])$(id).disabled=generating||delivering||!selected.size;
  for(const id of ['image-select-all','image-clear','image-category'])$(id).disabled=generating||delivering;
  $('image-zip').disabled=generating||delivering||!files.length;
 }
 function clear(){for(const url of urls)URL.revokeObjectURL(url);urls=[];files=[];selected.clear();$('image-previews').replaceChildren();update();}
 async function perform(action,indices=selected){
  if(delivering||generating)return;delivering=true;$('mobile-image-dialog').dataset.busy='true';$('image-close').disabled=true;update();
  $('image-status').textContent=action==='share'?'正在准备系统分享…':'正在保存到相册…';
  try{
   const result=await LongImage.deliver(files,new Set(indices),action,window.AndroidBridge,count=>{$('image-status').textContent=`已保存 ${count} 张到相册…`;});
   $('image-status').textContent=result.opened?'已打开系统分享，请在选择器中确认。':`已保存 ${result.count} 张到相册「星落抽卡手记」。`;
  }catch(error){$('image-status').textContent=(error.completed?`已保存 ${error.completed} 张，后续保存中止。`:'')+(MobileServices.messages[error.message]||error.message||'图片操作失败，请重试。');}
  finally{delivering=false;delete $('mobile-image-dialog').dataset.busy;$('image-close').disabled=false;update();}
 }
 function preview(file,index){
  const item=el('article',undefined,'image-preview'),heading=el('div',undefined,'image-preview-heading'),label=el('label'),choice=document.createElement('input');
  choice.type='checkbox';choice.checked=true;choice.setAttribute('aria-label',`选择第 ${index+1} 张图片`);choice.addEventListener('change',()=>{choice.checked?selected.add(index):selected.delete(index);update();});
  label.append(choice,document.createTextNode(`第 ${index+1} 张`));heading.append(label,el('small',`${file.width} × ${file.height}`));
  const actions=el('div',undefined,'image-preview-actions'),save=el('button','保存','button secondary'),share=el('button','分享','button secondary');save.type=share.type='button';save.addEventListener('click',()=>perform('album',new Set([index])));share.addEventListener('click',()=>perform('share',new Set([index])));actions.append(save,share);
  const details=document.createElement('details'),summary=el('summary','预览图片'),image=document.createElement('img');image.src=file.url;image.alt=`抽卡记录长图第 ${index+1} 张`;details.append(summary,image);item.append(heading,actions,details);return item;
 }
 async function generate(){
  const run=++generation;generating=true;clear();update();$('image-status').textContent='正在本机生成图片…';
  try{
   await document.fonts.ready;const pages=LongImage.planPages(report,category),base=`星落-${(report.account?.label||'招募记录').replace(/[<>:"/\\|?*]/g,'_')}`;
   for(let index=0;index<pages.length;index++){
    if(run!==generation||!$('mobile-image-dialog').open)break;
    const canvas=LongImage.draw(report,pages[index],index,pages.length,category),blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png')),width=canvas.width,height=canvas.height;canvas.width=canvas.height=1;
    if(!blob)throw new Error('图片生成失败，请减少记录后重试。');
    if(run!==generation||!$('mobile-image-dialog').open)break;
    const url=URL.createObjectURL(blob),file={name:`${base}-${String(index+1).padStart(2,'0')}.png`,blob,url,width,height};urls.push(url);files.push(file);selected.add(files.length-1);$('image-previews').append(preview(file,index));
    $('image-status').textContent=`已生成 ${index+1}/${pages.length} 张图片。`;update();await new Promise(resolve=>requestAnimationFrame(resolve));
   }
   if(files.length)$('image-status').textContent=`已生成 ${files.length} 张图片，默认全部选中。`;
  }catch(error){if(run===generation)$('image-status').textContent=error.message||'图片生成失败，请重试。';}
  finally{if(run===generation){generating=false;update();}}
 }
 function open(value,selectedCategory='all'){
  if(!value){notify('没有可生成长图的记录。','error');return;}report=value;category=selectedCategory;$('image-category').value=category;
  if(!$('mobile-image-dialog').open)$('mobile-image-dialog').showModal();generate();
 }
 function init(options={}){notify=options.notify||notify;
  $('image-category').addEventListener('change',()=>{category=$('image-category').value;if(report)generate();});
  $('image-select-all').addEventListener('click',()=>{selected=new Set(files.map((file,index)=>index));for(const choice of $('image-previews').querySelectorAll('input[type=checkbox]'))choice.checked=true;update();});
  $('image-clear').addEventListener('click',()=>{selected.clear();for(const choice of $('image-previews').querySelectorAll('input[type=checkbox]'))choice.checked=false;update();});
  $('image-save').addEventListener('click',()=>perform('album'));$('image-share').addEventListener('click',()=>perform('share'));
  $('image-zip').addEventListener('click',async()=>{if(!files.length||delivering)return;$('image-zip').disabled=true;try{const zip=await LongImage.makeZip(files),base=`星落-${(report.account?.label||'招募记录').replace(/[<>:"/\\|?*]/g,'_')}`;MobileServices.saveBlob(zip,`${base}-长图.zip`);}catch{notify('压缩包生成失败，仍可逐张保存图片。','error');}finally{update();}});
  $('image-close').addEventListener('click',()=>{if(!delivering)$('mobile-image-dialog').close();});
  $('mobile-image-dialog').addEventListener('cancel',event=>{if(delivering)event.preventDefault();});
  $('mobile-image-dialog').addEventListener('close',()=>{generation++;generating=false;clear();});
 }
 return {init,open};
})();
