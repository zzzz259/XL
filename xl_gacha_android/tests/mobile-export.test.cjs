'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const MobileServices=require('../web/mobile-services.js');

function fixture(){
 return {account:{id:'account-test',label:'主号/测试',uid:'7',channel:'sms'},records:[{time_beijing:'2026-01-02 03:04:05',pool_name:'=危险卡池',index:1,record_id:99,pool_id:1,drops:[{item_id:2,name:'+角色',rarity:5,count:1}]}],analysis:{pools:[{pool_id:1,category:'limited'}]}};
}

test('JSON export preserves the complete report and a safe deterministic filename',()=>{
 const report=fixture(),file=MobileServices.reportFile(report,'json',true,new Date('2026-10-26T01:02:03Z'));
 assert.deepEqual(JSON.parse(file.content),report);
 assert.equal(file.mime,'application/json');
 assert.equal(file.filename,'demo-starfall-gacha-主号_测试-2026-10-26.json');
});

test('CSV export keeps identities and neutralizes spreadsheet formulas',()=>{
 const file=MobileServices.reportFile(fixture(),'csv',false,new Date('2026-10-26T01:02:03Z'));
 assert.ok(file.content.startsWith('\ufeff'));
 assert.ok(file.content.includes('"=危险卡池"')===false);
 assert.ok(file.content.includes('"\'=危险卡池"'));
 assert.ok(file.content.includes('"\'+角色"'));
 assert.ok(file.content.includes('"account-test"'));
 assert.ok(file.content.includes('"limited"'));
 assert.equal(file.mime,'text/csv;charset=utf-8');
});

test('unsupported export types and missing reports fail before creating a file',()=>{
 assert.throws(()=>MobileServices.reportFile(fixture(),'xml'),/不支持的导出格式/);
 assert.throws(()=>MobileServices.reportFile(null,'json'),/没有可导出的记录/);
});

test('shared long-image engine loads without the legacy desktop dialog',()=>{
 const fs=require('node:fs'),vm=require('node:vm');
 const code=fs.readFileSync('../xl_gacha_web/static/image-export.js','utf8');
 const context={module:{exports:{}},exports:{},document:{getElementById:()=>null},window:{},TextEncoder};
 vm.runInNewContext(code,context);
 assert.equal(typeof context.module.exports.planPages,'function');
 assert.equal(typeof context.module.exports.deliver,'function');
});

test('catalog feedback recognizes native updates and actual revision changes',()=>{
 assert.equal(MobileServices.catalogView({phase:'current',revision:'new',updated:false},'old').updated,true);
 assert.equal(MobileServices.catalogView({phase:'current',revision:'same',updated:false},'same').updated,false);
 assert.equal(MobileServices.catalogView({phase:'current',revision:'cached',updated:false},undefined).updated,false);
 assert.equal(MobileServices.catalogView({phase:'current',revision:'new',updated:true},'old').updated,true);
});
