'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const MobileProbability=require('../web/mobile-probability.js');

test('first-UP distribution covers two pity cycles and preserves the documented boundaries',()=>{
 const rows=MobileProbability.distribution();
 assert.equal(rows.length,140);
 assert.ok(rows.at(-1).cumulative>0.999);
 assert.equal(rows[0].probability,0.01);
 assert.ok(Math.abs(rows[2].cumulative-0.029996)<1e-9);
 assert.ok(Math.abs(rows[69].cumulative-0.72502305)<1e-8);
 assert.equal(MobileProbability.thresholds.luckyMax,6);
 assert.equal(MobileProbability.thresholds.unluckyMin,122);
});

test('event grades use the distribution and never punish guaranteed or uncertain pulls',()=>{
 const event=(pulls,guaranteed=false,exact=true)=>({pulls,guaranteed,interval_exact:exact});
 assert.equal(MobileProbability.eventGrade(event(6),70),'lucky');
 assert.equal(MobileProbability.eventGrade(event(7),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(70),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(121),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(122),70),'rough');
 assert.equal(MobileProbability.eventGrade(event(122,true),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(122,null),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(1,false,false),70),'normal');
 assert.equal(MobileProbability.eventGrade(event(122),null),'normal');
 assert.equal(MobileProbability.eventGrade(event(2),70,'normal'),'normal');
});
