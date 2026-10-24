const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
const start=source.indexOf('function displayLabel(');
const display=vm.runInNewContext(source.slice(start,source.indexOf('\nconst dateText',start))+'\ndisplayLabel');
test('long bilingual Queens address becomes a useful place and street label',()=>{
 assert.equal(display('Queens Center, 90-15, Queens Boulevard, Elmhurst, Queens, Queens County, 纽约;紐約, 纽约州;紐約州, 11373, 美国;美國'),'Queens Center · 90-15 Queens Boulevard, Elmhurst, NY');
});
test('pin coordinates and short place labels stay intact',()=>{
 assert.equal(display('40.734596, -73.870012'),'40.734596, -73.870012');
 assert.equal(display('Neighborhood Service Plaza'),'Neighborhood Service Plaza');
});
test('Census address retains its street, city and state',()=>{
 assert.equal(display('1600 PENNSYLVANIA AVE NW, WASHINGTON, DC, 20500'),'1600 PENNSYLVANIA AVE NW, WASHINGTON, DC');
});
