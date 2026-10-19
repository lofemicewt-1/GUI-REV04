// Execute compiled AVR firmware and the real Wire rate/recovery implementation.
// CPU simulation only: UART is hooked; no physical bus, board or upload.
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const root = path.resolve(__dirname, '..');
const out = path.join(root, 'outputs/button_mode_20261019');
const { CPU, avrInstruction } = require(path.join(root, 'outputs/type4_20260930/avr_validation/node_modules/avr8js'));
const entries = fs.readFileSync(path.join(out, 'avr_symbols.txt'), 'utf8')
  .split(/\r?\n/).map(line => /^([0-9a-f]+)\s+\w\s+(.*)$/.exec(line.trim())).filter(Boolean)
  .map(m => ({ addr: parseInt(m[1], 16), name: m[2] }));
function symbol(name) {
  const item = entries.find(s => s.name === name);
  assert(item, `Missing symbol ${name}`);
  return item.addr >= 0x800000 ? item.addr - 0x800000 : item.addr / 2;
}
const image = new Uint8Array(32768);
let upper = 0;
for (const line of fs.readFileSync(path.join(out, 'build_validation/I2cSpeedValidation.ino.hex'), 'utf8').split(/\r?\n/)) {
  if (!line.startsWith(':')) continue;
  const record = Buffer.from(line.slice(1), 'hex');
  const count = record[0], address = (record[1] << 8) | record[2], type = record[3];
  if (type === 4) upper = ((record[4] << 8) | record[5]) << 16;
  if (type === 0) image.set(record.subarray(4, 4 + count), upper + address);
}
const cpu = new CPU(new Uint16Array(image.buffer), 2048);
const addr = Object.fromEntries(['testCommand', 'testDone', 'testReady', 'testText', 'currentI2cClockHz']
  .map(name => [name, symbol(name)]));
const uartPc = symbol('HardwareSerial::write(unsigned char)');
let uart = '';
function step() {
  if (cpu.pc === uartPc) {
    uart += String.fromCharCode(cpu.data[22]);
    cpu.data[24] = 1; cpu.data[25] = 0;
    const sp = cpu.SP + 2;
    cpu.pc = (cpu.data[sp - 1] << 8) | cpu.data[sp]; cpu.SP = sp;
  } else avrInstruction(cpu);
}
function until(predicate) {
  for (let i = 0; i < 3000000; i++) {
    if (predicate()) return;
    step();
  }
  throw new Error(`AVR execution stalled at ${(cpu.pc * 2).toString(16)}; UART=${uart}`);
}
function operation(command) {
  const done = cpu.data[addr.testDone];
  cpu.data[addr.testCommand] = command;
  until(() => cpu.data[addr.testDone] !== done);
}
function command(text) {
  const bytes = Buffer.from(text + '\0');
  assert(bytes.length <= 36);
  cpu.data.set(bytes, addr.testText); uart = ''; operation(0); return uart;
}
const TWBR0 = 0xB8, TWSR0 = 0xB9, TWCR0 = 0xBC;
function checkRate(hz, twbr) {
  assert.equal(cpu.data[TWBR0], twbr, 'actual TWBR0');
  assert.equal(cpu.data[TWSR0] & 3, 0, 'actual TWI0 prescaler');
  assert.equal(cpu.data[TWCR0] & 5, 5, 'Wire retains TWEN0 and TWIE0');
  assert.equal(cpu.dataView.getUint32(addr.currentI2cClockHz, true), hz);
}
until(() => cpu.data[addr.testReady] === 42);
checkRate(400000, 12);
assert.equal(command('I2CSPD?'), '^I2CSPD 400000~');
const checks = ['boot default 400 kHz; TWBR0=12, prescaler=1, Wire interrupt enabled'];
const rates = [[100000, 72], [400000, 12], [500000, 8], [615385, 5],
  [666667, 4], [727273, 3], [800000, 2]];
for (const [speed, twbr] of [...rates, ...rates.slice().reverse(), [800000, 2]]) {
  cpu.data[TWSR0] |= 3; // Confirm the production setter clears stale prescaler bits.
  assert.equal(command(`I2CSPD ${speed}`), `^I2CSPD ${speed}~`);
  checkRate(speed, twbr);
  assert.equal(command('I2CSPD?'), `^I2CSPD ${speed}~`);
  operation(2); // Real MiniCore twi_handleTimeout(true), without a physical bus.
  checkRate(speed, twbr);
}
checks.push('All seven rates switch/query in ascending and descending order; TWBR0=72/12/8/5/4/3/2');
checks.push('real Wire timeout reinitialization preserves each selected rate and TWEN0/TWIE0');
for (const text of ['I2CSPD', 'I2CSPD 0', 'I2CSPD -1', 'I2CSPD 600000',
  'I2CSPD 615384', 'I2CSPD 666666', 'I2CSPD 888888',
  'I2CSPD 888889', 'I2CSPD 1000000', 'I2CSPD 1000001', 'I2CSPD 800000 extra', 'I2CSPD nope', 'I2CSPD 4294967296']) {
  assert.equal(command(text), '^ERR I2CSPD DENY~', text);
  checkRate(800000, 2);
}
assert.equal(command('I2CSPD? extra'), '^ERR I2CSPD FORMAT~');
checkRate(800000, 2);
checks.push('invalid/missing/overflow/trailing arguments rejected without changing selected rate');
operation(1);
checkRate(400000, 12);
checks.push('explicit bus initialization restores the safe default of 400 kHz');
const result = { status: 'PASS', target: 'ATmega328PB 16 MHz', checks,
  boundary: 'AVR CPU execution and registers only; physical I2C and signal timing untested' };
fs.writeFileSync(path.join(out, 'avr_validation_result.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
