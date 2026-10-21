const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
const start=source.indexOf('function searchFeedback(');
const feedback=vm.runInNewContext(source.slice(start,source.indexOf('\nfunction updateSearchFeedback',start))+'\nsearchFeedback');
const scan={created:1000,run_started_at:2000,status:'running',message:'读取区域 1 / 1',progress:{done:0,total:1},activity:{phase:'query',phase_started_at:2000,provider:'primary.example',attempt:1,attempts:2}};
test('waiting search reports real elapsed time and a slow service without invented progress',()=>{
  const result=feedback(scan,2068,2067);
  assert.match(result,/1:08/);assert.match(result,/0\/1/);assert.match(result,/68 秒/);assert.match(result,/primary.example/);assert.match(result,/可取消/);
  assert.ok(!result.includes('%'));
});
test('fallback, receiving bytes, filtering and cancellation are distinguishable',()=>{
  assert.match(feedback({...scan,activity:{...scan.activity,attempt:2,provider:'backup.example'}},2005,2005),/备用服务.*backup.example/);
  assert.match(feedback({...scan,activity:{phase:'download',bytes:4096}},2005,2005),/接收.*4 KB/);
  assert.match(feedback({...scan,activity:{phase:'filter',elements:683}},2005,2005),/处理 683/);
  assert.match(feedback({...scan,cancel:true},2005,2005),/正在取消/);
  assert.match(feedback(scan,2068,2000),/连接暂时中断/);
});
