'use strict';

const MobileNavigation=(()=>{
 const pages=new Set(['records','query','profile']);
 function initialPage({report,accounts=[]}){return report||accounts.length?'records':'query';}
 function createNavigation(initial){
  if(!pages.has(initial))throw new Error('Unknown page');
  const history=[initial],positions={records:0,query:0,profile:0};
  return {
   get page(){return history[history.length-1];},
   go(page,{replace=false,resetScroll=false,scrollY=0}={}){
    if(!pages.has(page))throw new Error('Unknown page');
    const current=history[history.length-1];positions[current]=scrollY;
    if(resetScroll)positions[page]=0;
    if(page!==current){
     if(replace){history.pop();if(history[history.length-1]!==page)history.push(page);}
     else history.push(page);
    }
    return positions[page];
   },
   back(scrollY=0){
    if(history.length<2)return null;positions[history[history.length-1]]=scrollY;history.pop();
    const page=history[history.length-1];return {page,scrollY:positions[page]};
   },
  };
 }
 return {initialPage,createNavigation};
})();

if(typeof module!=='undefined')module.exports=MobileNavigation;
