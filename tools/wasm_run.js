// WASM 后端验证运行器（Node）：node wasm_run.js FILE.wasm [期望输出]
const fs = require('fs');

const path = process.argv[2];
const expect = process.argv[3] || null;
const buf = fs.readFileSync(path);
let parts = [];
let mod = null;

const imports = {
  env: {
    print_begin() { parts = []; },
    print_i64(v) { parts.push(String(v)); },
    print_f64(v) {
      // 与解释器同口径：整数值保留 .0（3.0 而不是 3），其余最短可逆表示
      const s = (Number.isFinite(v) && Math.floor(v) === v && Math.abs(v) < 1e16)
        ? v.toFixed(1) : String(v);
      parts.push(s);
    },
    print_str(ptr, len) {
      if (process.env.WASM_DEBUG) process.stderr.write('str p=' + ptr + ' l=' + len + '\n');
      const mem = new Uint8Array(mod.exports.memory.buffer, ptr, len);
      parts.push(Buffer.from(mem).toString('utf8'));
    },
    print_nl() { console.log(parts.join(' ')); },
    overflow() { console.error('ImporE1012: 整数溢出'); }
  }
};

const lines = [];
const origLog = console.log;
console.log = (s) => { lines.push(String(s)); origLog(s); };

WebAssembly.instantiate(buf, imports).then((r) => {
  mod = r.instance;
  mod.exports._start();
  if (expect !== null) {
    const got = lines.join('\n').trim();
    if (got !== expect.trim()) {
      console.error('MISMATCH got=' + JSON.stringify(got) + ' want=' + JSON.stringify(expect));
      process.exit(2);
    }
    console.log('MATCH');
  }
}).catch((e) => {
  console.error('ERR ' + (e && e.message ? e.message : e));
  process.exit(4);
});
