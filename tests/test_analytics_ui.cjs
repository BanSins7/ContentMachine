'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '..', 'site', 'analytics.js'), 'utf8');
class Elem {
  constructor(id) { this.id = id; this.value = ''; this.options = []; this.listeners = {}; this.disabled = false; this.style={}; }
  addEventListener(name, callback) {this.listeners[name] = callback;}
  replaceChildren() { this.options=[]; this.children=[];}
  append(...children) {this.children=(this.children||[]).concat(children);if(this.id==='analyticsProject'){this.options.push(...children);if(!this.value && children[0])this.value=children[0].value;}}
}
const ids = ['analyticsPanel','analyticsNotice','analyticsProject','analyticsSource',
 'analyticsVideoIds','analyticsRun','analyticsBatch','analyticsWork','analyticsRefresh',
 'analyticsSnapshots','analyticsJobs','analyticsAggregate','analyticsAggregateResults',
 'analyticsConfigId','analyticsMinSample','analyticsDimension'];
const els = Object.fromEntries(ids.map(x=>[x,new Elem(x)]));
els.analyticsVideoIds.value='7,8';
els.analyticsSource.value='legal';
els.analyticsConfigId.value='3';
els.analyticsMinSample.value='2';
els.analyticsDimension.value='video_format';
const requests=[];
const fetchMock=async (url,options={})=>{
 requests.push({url,options});
 const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});
 if(url==='/api/projects')return response({ok:true,projects:[{id:'a'.repeat(32),name:'Project'}]});
 if(url.includes('/analytics/batch'))return response({ok:true,jobs:[{id:10},{id:11}]},202);
 if(url.includes('/analytics/jobs/work'))return response({ok:true,processed:1,jobs:[{id:10}]});
 if(url.includes('/analytics/aggregate?'))return response({ok:true,summary:{segments:[]}});
 if(url.includes('/analytics/qualify'))return response({ok:true,snapshot:{status:'partial',blockers:['no_approved_relevance']}});
 if(url.includes('/analytics/jobs?'))return response({ok:true,jobs:[]});
 if(url.includes('/analytics?'))return response({ok:true,results:[]});
 throw Error('Unexpected endpoint '+url);
};
let onReady;
const document={
 getElementById:id=>els[id]||null,
 createElement:type=>new Elem(type),
 addEventListener:(evt,cb)=>{if(evt==='DOMContentLoaded')onReady=cb;},
};
const sandbox={document,fetch:fetchMock,URLSearchParams,Date,Math,console};
vm.runInNewContext(code,sandbox,{filename:'site/analytics.js'});
async function run() {
 onReady();
 await new Promise(r=>setImmediate(r));
 assert.equal(els.analyticsProject.value,'a'.repeat(32));
 // The test runs the actual registered browser handlers, not just syntax.
 await els.analyticsBatch.listeners.click();
 assert.ok(requests.some(x=>x.url.endsWith('/analytics/batch') && x.options.method==='POST'));
 await els.analyticsWork.listeners.click();
 assert.ok(requests.some(x=>x.url.endsWith('/analytics/jobs/work') && x.options.method==='POST'));
 els.analyticsVideoIds.value='7';
 await els.analyticsRun.listeners.click();
 assert.ok(requests.some(x=>x.url.endsWith('/analytics/qualify') && x.options.method==='POST'));
 await els.analyticsAggregate.listeners.click();
 assert.ok(requests.some(x=>x.url.includes('/analytics/aggregate?')));
 console.log('Analytics UI browser-handler smoke PASS');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
