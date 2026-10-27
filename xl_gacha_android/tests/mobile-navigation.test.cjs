'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const MobileNavigation=require('../web/mobile-navigation.js');

test('initial destination follows selected archive and library availability',()=>{
 assert.equal(MobileNavigation.initialPage({accounts:[],report:null}),'query');
 assert.equal(MobileNavigation.initialPage({accounts:[{id:'a'}],report:null}),'records');
 assert.equal(MobileNavigation.initialPage({accounts:[],report:{account:{id:'a'}}}),'records');
});

test('navigation restores the previous page and its scroll position',()=>{
 const nav=MobileNavigation.createNavigation('records');
 assert.equal(nav.go('query',{scrollY:120}),0);
 assert.equal(nav.go('profile',{scrollY:240}),0);
 assert.deepEqual(nav.back(360),{page:'query',scrollY:240});
 assert.deepEqual(nav.back(80),{page:'records',scrollY:120});
 assert.equal(nav.back(),null);
});

test('replace avoids duplicate record destinations and invalid pages fail closed',()=>{
 const nav=MobileNavigation.createNavigation('records');
 nav.go('query');nav.go('records',{replace:true});
 assert.equal(nav.page,'records');
 assert.equal(nav.back(),null);
 assert.throws(()=>nav.go('overview'));
 assert.throws(()=>MobileNavigation.createNavigation('pools'));
});
