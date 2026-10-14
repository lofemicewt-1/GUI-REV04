// Runs the compiled product C++ in an ATmega328P CPU emulator. I2C, EEPROM,
// UART and time are faked; no serial port is opened and no firmware is uploaded.
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const root = path.resolve(__dirname, '..');
const out = path.resolve(process.argv[2] || path.join(root, 'outputs/type4_20260930'));
const { CPU, avrInstruction } = require(path.join(root, 'outputs/type4_20260930/avr_validation/node_modules/avr8js'));
const symbols = fs.readFileSync(path.join(out, 'avr_symbols.txt'), 'utf8');
const entries = symbols.split(/\r?\n/).map(line => /^([0-9a-f]+)\s+(\w)\s+(.*)$/.exec(line.trim())).filter(Boolean)
  .map(m => ({ addr:parseInt(m[1],16), kind:m[2], name:m[3] }));
const find = name => {
  const item = entries.find(s => s.name === name);
  if (!item) throw new Error(`Missing symbol: ${name}`);
  return item.addr >= 0x800000 ? item.addr - 0x800000 : item.addr / 2;
};
const bytes = new Uint8Array(32768);
let upper = 0;
for (const line of fs.readFileSync(path.join(out,'build_validation/Type4Validation.ino.hex'),'utf8').split(/\r?\n/)) {
  if (!line.startsWith(':')) continue;
  const record = Buffer.from(line.slice(1),'hex');
  const size = record[0], address = (record[1]<<8) | record[2], type = record[3];
  if (type === 4) upper = ((record[4]<<8) | record[5]) << 16;
  if (type === 0) bytes.set(record.subarray(4,4+size), upper + address);
}
const prog = new Uint16Array(bytes.buffer);
const cases = JSON.parse(fs.readFileSync(path.join(out,'avr_cases.json'),'utf8'));
const addr = Object.fromEntries(['testCommand','testDone','testResult','testReady','testSlot','testLength','testAddress','testText']
  .map(name => [name,find(name)]));
const checks = [];

class Machine {
  constructor(eeprom = Buffer.alloc(1024,255)) {
    this.cpu = new CPU(prog,2048);
    this.eeprom = Buffer.from(eeprom);
    this.regs = new Map(); this.sram = new Map(); this.writes = []; this.eepromWrites = 0;
    this.minimumSP = 0x8ff; this.uart = ''; this.time = 0; this.button = -1; this.cut = -1;
    this.failWrite = -1; this.writeCount = 0;
    this.hooks = new Map();
    this.add('eeprom_read_byte', () => { this.cpu.data[24] = this.eeprom[this.r16(24)]; });
    this.add('eeprom_write_byte', () => this.update(this.r16(24), this.cpu.data[22]));
    // The library lowers EEPROM.update to this clone, preserving its address/data ABI.
    for (const s of entries.filter(s => s.name.startsWith('EEPROMClass::update(')))
      this.hooks.set(s.addr/2, () => this.update(this.r16(24),this.cpu.data[22]));
    this.add('write_single(unsigned char, unsigned char, unsigned int)', () => {
      const dev=this.cpu.data[24], reg=this.cpu.data[22], value=this.r16(20);
      this.writeCount++;
      const ok = this.writeCount !== this.failWrite;
      this.writes.push({dev,reg,value,ok});
      if (ok) {
        if (reg === 0) {
          const pointer=this.regs.get(0x15)&0x3ff;
          this.sram.set(pointer,value);
          this.regs.set(0x15,(this.regs.get(0x15)&~0x3ff)|((pointer+1)&0x3ff));
        } else this.regs.set(reg,value);
      }
      this.cpu.data[24]=+ok;
    });
    this.add('read_single_safe(unsigned char, unsigned char, unsigned int*)', () => {
      const reg = this.cpu.data[22], ptr=this.r16(20);
      let value = this.regs.has(reg) ? this.regs.get(reg) : 0xB59D;
      if (reg === 0x16) value = 0x1221;
      if (reg === 0x1e) value = 0x5081;
      if (reg === 0x2b) value = 0x1018;
      this.cpu.dataView.setUint16(ptr,value,true); this.cpu.data[24]=1;
    });
    this.add('HardwareSerial::write(unsigned char)', () => {this.uart += String.fromCharCode(this.cpu.data[22]); this.set16(24,1);});
    this.add('millis', () => this.set32(22,this.time));
    this.add('delay', () => {});
    this.add('analogRead', () => this.set16(24,this.cpu.data[24] === this.button ? 0 : 1023));
    this.runUntil(() => this.cpu.data[addr.testReady] === 42);
    this.run(100);
  }
  add(name,fn) {const s=entries.find(s=>s.name===name); if(s)this.hooks.set(s.addr/2,fn);}
  r16(r) {return this.cpu.data[r]|(this.cpu.data[r+1]<<8);}
  set16(r,v) {this.cpu.data[r]=v; this.cpu.data[r+1]=v>>8;}
  set32(r,v) {for(let i=0;i<4;i++)this.cpu.data[r+i]=v>>>(8*i);}
  update(address,value) {
    assert(address>=0 && address<1024,'EEPROM address outside Nano');
    if (this.eeprom[address] === value) return;
    this.eeprom[address] = value; this.eepromWrites++;
    if (this.eepromWrites === this.cut) throw new Error('POWER_CUT');
  }
  step() {
    const hook=this.hooks.get(this.cpu.pc);
    if (hook) {
      hook(); const sp=this.cpu.SP+2;
      this.cpu.pc=(this.cpu.data[sp-1]<<8)|this.cpu.data[sp]; this.cpu.SP=sp;
    } else avrInstruction(this.cpu);
    this.minimumSP=Math.min(this.minimumSP,this.cpu.SP);
  }
  run(n) {for(let i=0;i<n;i++)this.step();}
  runUntil(test) {
    for(let i=0;i<3000000;i++) {if(test())return;this.step();}
    throw new Error(`AVR timed out pc=${(this.cpu.pc*2).toString(16)} uart=${this.uart}`);
  }
  command(command,slot=0) {
    const done=this.cpu.data[addr.testDone];
    this.cpu.data[addr.testSlot]=slot; this.cpu.data[addr.testCommand]=command;
    this.runUntil(()=>this.cpu.data[addr.testDone] !== done);
    return this.cpu.data[addr.testResult];
  }
  cfg(text) {
    const data=Buffer.from(text+'\0'); assert(data.length<=36);
    this.cpu.data.set(data,addr.testText); this.uart=''; this.command(3); return this.uart;
  }
}
function put(machine,slot,hex) {machine.eeprom.set(Buffer.from(hex,'hex'),256+slot*256);}

let lowWater = 0x8ff;
for(const c of cases.cases) {
  for (const initialSample of [0x0000,0x0400,0x0800,0x0C00]) {
    const m=new Machine(); put(m,0,c.raw);
    const initial05=(0xEFBD&~0x0C00)|initialSample;
    for(const [reg,value] of [[5,initial05],[6,0xDFFF],[0x12,0xBEA5],[0x15,0xF081]])m.regs.set(reg,value);
    assert.equal(m.command(1),0,'Type4 apply result');
    assert.equal(m.writes.filter(w=>w.reg===0x1f).length,1);
    assert.equal(m.regs.get(0x1f),0xC);
    assert.equal(m.regs.get(5),(initial05&~0xEC03)|(c.zero<<13)|2,
      'Type4 forces 0x05[11:10]=00 while retaining Zero Time, other fields, and SRAM mode');
    assert.equal(m.regs.get(5)&0x0C00,0,'Type4 fixed sample update is 4 us');
    assert.equal(m.regs.get(6),(0xDFFF&~0xFC)|(c.press<<5)|(c.release<<2));
    assert.equal(m.regs.get(0x12),0xBEA5);
    assert.equal(m.regs.get(0x15),0xF081);
    for(const [slice,words] of c.descriptors)for(let i=0;i<4;i++)assert.equal(m.sram.get(slice*4+i),words[i],`slice${slice} word${i}`);
    for(let stage=0;stage<2;stage++) {
      const base=8+stage*5;
      assert.equal(m.regs.get(base),(0xB59D&~0xFF33)|0x3300|(stage?0x22:0x11));
      for(let event=0;event<2;event++) {
        const mask=0xE000|(event===stage?0xFFF:0);
        assert.equal(m.regs.get(base+1+event),(0xB59D&~mask)|0x4000|(stage?0:0x2000)|(event===stage?c.thresholds[stage]:0));
        assert.equal(m.regs.get(base+1+event)&0x8000,0,'Type4 disables press/release debounce');
      }
      const start=c.starts[c.refs[stage]], repeat=c.repeats[stage];
      assert.equal(m.regs.get(base+4),(repeat<<13)|(start<<8)|(repeat<<5)|start);
    }
    assert.equal(m.regs.get(2),1);
    lowWater=Math.min(lowWater,m.minimumSP);
  }
}
checks.push('32 compiled Type4 driver cases (initial 4/8/16/32 us) matched descriptor words and register masks; all forced sample update to 4 us');
for (const initiallyEnabled of [false,true]) {
  const m=new Machine(); put(m,0,cases.cases[0].raw);
  const registers=[9,10,14,15];
  for (const reg of registers) m.regs.set(reg,(initiallyEnabled?0x8000:0)|0x159D);
  m.regs.set(0x12,0xBEA5);
  assert.equal(m.command(1),0);
  assert.equal(m.regs.get(5)&0x0C00,0,'Repeated Type4 apply keeps fixed 4 us sample update');
  for (const reg of registers) {
    assert.equal(m.regs.get(reg)&0x8000,0);
    assert.equal(m.regs.get(reg)&0x1000,0x1000,'Comparison direction must survive');
  }
  assert.equal(m.regs.get(0x12),0xBEA5,'Global debounce time remains untouched');
  for (const reg of registers) m.regs.set(reg,m.regs.get(reg)|0x8000);
  assert.equal(m.cfg('CFGAPPLY 0'),'^CFGAPPLY OK 0~');
  for (const reg of registers) assert.equal(m.regs.get(reg)&0x8000,0,'Repeated apply disables debounce again');
}
checks.push('Type4 clears all four Stage0/1 debounce bits from both initial states and on repeated apply; direction and global time preserved');

// Every changed EEPROM byte is an interruption point; reboot resumes actual C++ migration.
const old=Buffer.alloc(1024,255); old[0]=1;
old.set(Buffer.from(cases.legacy[0],'hex'),256);
old.set(Buffer.from(cases.legacy[1],'hex'),384);
old.set(Buffer.from(cases.legacy[0],'hex'),512);
const complete=new Machine(old); complete.command(2); const total=complete.eepromWrites;
for(let cut=1;cut<=total;cut++) {
  const interrupted=new Machine(old); interrupted.cut=cut;
  assert.throws(()=>interrupted.command(2),/POWER_CUT/);
  const reboot=new Machine(interrupted.eeprom); reboot.command(2);
  assert.deepEqual(reboot.eeprom,complete.eeprom,`Migration reboot after write${cut}`);
}
assert.equal(complete.eeprom[0],1);
assert.equal(complete.cfg('CFGREAD 0'),`^CFGREAD 0 ${cases.legacy[0].toUpperCase()} CRC=${cases.legacyCrc[0].toString(16).toUpperCase().padStart(4,'0')}~`);
assert.equal(complete.cfg('CFGREAD 1'),`^CFGREAD 1 ${cases.legacy[1].toUpperCase()} CRC=${cases.legacyCrc[1].toString(16).toUpperCase().padStart(4,'0')}~`);
const saved=Buffer.from(complete.eeprom); complete.command(2); assert.deepEqual(complete.eeprom,saved);
checks.push(`Migration resumed after all ${total} changed-byte power interruption points; active pointer and legacy readback preserved`);

function crc(raw) {let crc=65535;for(const v of raw){crc^=v<<8;for(let i=0;i<8;i++)crc=((crc<<1)^((crc&0x8000)?0x1021:0))&65535;}return crc;}
for (const legacy of cases.legacyType4 || []) {
  const m = new Machine(); m.command(2); put(m,0,legacy.raw);
  assert.equal(m.command(1),0,'Persisted legacy Type4 must still apply');
  assert.equal(m.sram.get(0),0x8C0 | (legacy.kind<<3));
  const record=Buffer.from(legacy.raw,'hex'), sum=crc(record).toString(16).toUpperCase().padStart(4,'0');
  assert.equal(m.cfg('CFGREAD 0'),`^CFGREAD 0 ${legacy.raw.toUpperCase()} CRC=${sum}~`);
  const before = Buffer.from(m.eeprom);
  assert.equal(m.cfg(`CFGBEGIN 0 256 ${sum}`),'^CFGBEGIN OK~');
  for(let offset=0;offset<256;offset+=11)
    assert.equal(m.cfg(`CFGDATA ${offset} ${record.subarray(offset,offset+11).toString('hex')}`),'^CFGDATA OK~');
  assert.match(m.cfg('CFGEND 0'),/FORMAT/,'Excluded kinds cannot be newly saved');
  assert.deepEqual(m.eeprom,before,'Rejected save must preserve the existing record');
}
if (cases.legacyType4) checks.push('Persisted Gauss/Ramp/DC Type4 remains readable/applicable; new CFG saves reject them without EEPROM mutation');
for(const [length,hex] of [[128,cases.legacy[0]],[128,cases.legacy[1]],[256,cases.cases[6].raw]]) {
  const m=new Machine(); m.command(2);
  const record=Buffer.from(hex,'hex'), sum=crc(record).toString(16).toUpperCase().padStart(4,'0');
  assert.equal(m.cfg(`CFGBEGIN 2 ${length} ${sum}`),'^CFGBEGIN OK~');
  const before=Buffer.from(m.eeprom), writesBefore=m.eepromWrites;
  for(let offset=0;offset<length;offset+=11)assert.equal(m.cfg(`CFGDATA ${offset} ${record.subarray(offset,offset+11).toString('hex')}`),'^CFGDATA OK~');
  assert.equal(m.eepromWrites,writesBefore,'RAM upload must not wear EEPROM');
  assert.deepEqual(m.eeprom,before);
  assert.equal(m.cfg('CFGEND 2'),`^CFGEND OK CRC=${sum}~`);
  assert.equal(m.eeprom[0],255,'Save never sets active');
  assert.equal(m.cfg('CFGREAD 2'),`^CFGREAD 2 ${hex.toUpperCase()} CRC=${sum}~`);
  assert.equal(m.writes.length,0,'Save/read-verification never apply chip registers');
  assert.equal(m.cfg('CFGUSE 2'),'^CFGUSE OK 2~');
  assert.equal(m.writes.length,0,'Set Active never applies');
  assert.equal(m.cfg('CFGAPPLY 2'),'^CFGAPPLY OK 2~');
  lowWater=Math.min(lowWater,m.minimumSP);
}
checks.push('Compiled CFG accepts 128/256 upload, stages solely in RAM, verifies CRC and keeps save/active separate');
for(const mask of [0,1,2,3,4,5,6,7]) {
  const image=Buffer.alloc(1024,255);image[0]=2;
  for(let slot=0;slot<3;slot++)if(mask&(1<<slot))image.set(Buffer.from(cases.legacy[slot%2],'hex'),256+slot*128);
  const migrated=new Machine(image);migrated.command(2);
  for(let slot=0;slot<3;slot++) {
    const record=migrated.eeprom.subarray(256+slot*256,384+slot*256);
    assert.deepEqual(record,mask&(1<<slot)?Buffer.from(cases.legacy[slot%2],'hex'):Buffer.alloc(128,255));
  }
  assert.equal(migrated.eeprom[0],2);
}
checks.push('All 8 occupied/empty legacy slot combinations migrated without shifting slot identity');
const rejected=new Machine();rejected.command(2);
put(rejected,0,cases.cases[4].raw);
const unchanged=Buffer.from(rejected.eeprom);
assert.equal(rejected.cfg('CFGBEGIN 0 256 0000'),'^CFGBEGIN OK~');
assert.match(rejected.cfg('CFGEND 0'),/FORMAT/);
const corrupt=Buffer.from(cases.cases[4].raw,'hex');
const sum=crc(corrupt).toString(16).padStart(4,'0');
assert.equal(rejected.cfg(`CFGBEGIN 0 256 ${sum}`),'^CFGBEGIN OK~');
corrupt[20]^=1;
for(let offset=0;offset<256;offset+=11)rejected.cfg(`CFGDATA ${offset} ${corrupt.subarray(offset,offset+11).toString('hex')}`);
assert.match(rejected.cfg('CFGEND 0'),/CRC/);assert.deepEqual(rejected.eeprom,unchanged);
for(const name of ['CFGREAD','CFGUSE','CFGAPPLY','CFGDEL'])assert.match(rejected.cfg(`${name} 256`),/SLOT/);
checks.push('Missing chunks, bad CRC and wrapped slot indices rejected without modifying the old record');
const boot=new Machine(); put(boot,1,cases.cases[4].raw);boot.eeprom[0]=1;
boot.command(4); assert.equal(boot.writes.length,0,'Type4 is not reset or applied at boot');
boot.command(5); boot.button=14; boot.time=100; boot.command(6); boot.time=140; boot.command(6);
assert.equal(boot.writes.filter(w=>w.reg===0x1f).length,1,'Board key must reload active Type4 once');
assert.equal(boot.eeprom[0],1);
const read=boot.cfg('CFGREAD 1');assert.match(read,/^\^CFGREAD 1 [0-9A-F]{512} CRC=[0-9A-F]{4}~$/);
assert.equal(boot.writes.filter(w=>w.reg===0x1f).length,1,'Transport read is passive; GUI read action invokes CFGAPPLY');
assert.equal(boot.cfg('CFGAPPLY 1'),'^CFGAPPLY OK 1~');
assert.equal(boot.writes.filter(w=>w.reg===0x1f).length,2);
checks.push('Boot skips Type4; physical key reloads active slot; each CFGAPPLY writes 0x1F exactly once');
const failure=new Machine();put(failure,0,cases.cases[4].raw);failure.regs.set(5,0xEFBD);failure.regs.set(0x15,0xF081);failure.failWrite=5;
assert.equal(failure.command(1),5,'I2C failure returns APPLY_WRITE');
assert.equal(failure.regs.get(5),(0xEFBD&~3)|2);
assert.equal(failure.regs.get(0x15),0xF081);
assert.equal(failure.writes.filter(w=>w.reg===2&&w.value===1).length,0);
checks.push('Descriptor I2C failure restores access mode/address and does not start detection');
lowWater=Math.min(lowWater,boot.minimumSP,failure.minimumSP,complete.minimumSP);

const report={checks,lowestSP:lowWater,remainingStackToStaticEnd:lowWater-find('__bss_end'),
              migrationChangedBytes:total,emulator:'avr8js',boundary:'Compiled AVR instructions; mocked EEPROM byte writes, I2C, UART and time; no hardware'};
fs.writeFileSync(path.join(out,'avr_validation_result.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify(report,null,2));
