const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
const start = source.indexOf('async function sharePlaza(');
const code = source.slice(start,source.indexOf('\nfunction showMapError',start));
const plaza = {id:'public-test',name:'Public place',lat:40.758,lng:-73.9855};
const button = () => ({innerHTML:'Share icon and text',disabled:false});
function load(extra){return vm.runInNewContext(code+'\nsharePlaza',{URL,Number,encodeURIComponent,...extra});}

test('native sheet opens in the click task before any network or dialog',async()=>{
  let payload,network=0,dialogs=0;
  const share=load({navigator:{share:data=>{payload=data;return Promise.resolve();}},post:()=>{network++;},showDestinationShare:()=>dialogs++});
  const b=button();const done=share(plaza,b);
  assert.equal(payload.url,'https://www.google.com/maps/place/40.7580000,-73.9855000');
  assert.equal(network,0);assert.equal(dialogs,0);
  await done;assert.equal(b.disabled,false);assert.equal(b.innerHTML,'Share icon and text');
});
test('cancelled native sheet does not open a second dialog',async()=>{
  let fallback=0;
  const share=load({navigator:{share:()=>Promise.reject({name:'AbortError'})},showDestinationShare:()=>fallback++});
  await share(plaza,button());assert.equal(fallback,0);
});
test('native failure retains a usable destination link without directions',async()=>{
  let result;
  const share=load({navigator:{share:()=>Promise.reject({name:'NotAllowedError'})},showDestinationShare:(p,data)=>result=data});
  await share(plaza,button());assert.ok(result.url.includes('/maps/place/'));assert.ok(!result.url.includes('/maps/dir'));
});
test('browser without native share uses verified short-link dialog',async()=>{
  let displayed;
  const share=load({navigator:{},state:{scan:{id:'scan'}},post:async()=>({url:'https://maps.app.goo.gl/GoogleIssuedTest',message:'verified'}),showDestinationShare:(p,data)=>displayed=data,toast:error=>{throw Error(error);}});
  await share(plaza,button());assert.equal(displayed.url,'https://maps.app.goo.gl/GoogleIssuedTest');
});
