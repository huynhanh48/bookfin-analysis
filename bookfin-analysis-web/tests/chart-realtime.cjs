const { test } = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('subscription cleanup, validation, reconnect and stale socket isolation', () => {
  const sockets = [], timers = [];
  class Socket {
    static OPEN = 1;
    readyState = 1;
    sent = [];
    constructor() { sockets.push(this); }
    send(text) { this.sent.push(JSON.parse(text)); }
    close() { this.readyState = 3; this.onclose?.({code:1000}); }
    emit(message) { this.onmessage?.({data:JSON.stringify(message)}); }
  }
  const ctx = {exports:{}, require: () => ({applyProvisionalIndicators(){}, markConfirmed(){}}), WebSocket:Socket, URL,
    window:{location:{href:'http://127.0.0.1:5173'}},
    setTimeout:fn=>{timers.push(fn);return timers.length;}, clearTimeout:()=>{},
    setInterval:()=>1, clearInterval:()=>{},
  };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/chartRealtime.ts'),'utf8'), {
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
  }).outputText,ctx);
  const client = new ctx.exports.ChartRealtime();
  const received = [];
  const candle = (channel, timestamp) => ({type:'candle',channel,data:{timestamp,open:10,high:12,low:9,close:11,volume:100}});
  client.subscribe('market:HPG:1D', x=>received.push(x), 100);
  const first = sockets[0]; first.emit({type:'ready'});
  assert.equal(first.sent[0].channel,'market:HPG:1D');
  first.emit(candle('market:HPG:1D',99)); assert.equal(received.length,0);
  first.emit(candle('market:HPG:1D',101)); assert.equal(received.length,1);
  client.subscribe('market:SSI:1W', x=>received.push(x), 100);
  first.emit(candle('market:HPG:1D',102)); assert.equal(received.length,1);
  const second = sockets[1]; second.emit({type:'ready'});
  second.emit(candle('market:HPG:1D',102)); assert.equal(received.length,1);
  second.emit(candle('market:SSI:1W',102)); assert.equal(received.length,2);
  second.onclose({code:1006}); timers.pop()();
  assert.equal(sockets.length,3);
  sockets[2].emit({type:'ready'});
  assert.equal(sockets[2].sent[0].channel,'market:SSI:1W');
  client.close();
  sockets[2].emit(candle('market:SSI:1W',103)); assert.equal(received.length,2);
});
