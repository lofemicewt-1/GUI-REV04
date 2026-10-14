"""Build a test sketch from the current product .cpp files; never touch hardware."""
from pathlib import Path
import json
import shutil
import sys
import argparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.arduino_preset_sync import V2Preset, pack_v2_preset, crc16_ccitt_false
from core.awp1221_preset import Awp1221WaveRecipe, awp1221_descriptor_words, allocate_awp1221_slices, pack_awp1221_payload
from core.awp1018_adc_preset import Awp1018AdcPresetModel, AWP1018WaveformDescriptor, pack_awp1018_adc_stage4_payload
from core.awp192x_button_runtime import Awp192xButtonPayloadV2, Awp192xSineWaveformRecipe, pack_awp192x_button_payload
from tests.test_awp1221_type4 import model

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, default=ROOT / "outputs/type4_20260930")
OUT = parser.parse_args().out.resolve()
sketch = OUT / "Type4Validation"
sketch.mkdir(parents=True, exist_ok=True)
for source in (ROOT / "outputs/Without_GUI").iterdir():
    if source.suffix in (".cpp", ".h"):
        shutil.copy2(source, sketch / source.name)
(sketch / "Type4Validation.ino").write_text('''#include "protocol.h"
#include "preset.h"
#include "awp1221.h"
volatile uint8_t testCommand = 255, testDone = 0, testResult = 255, testReady = 0;
volatile uint8_t testSlot = 0, testLength = 49;
volatile uint16_t testAddress = 264;
char testText[36];
void setup() { testReady = 42; }
void loop() {
  uint8_t command = testCommand;
  if (command == 255) return;
  if (command == 0) testResult = validateAwp1221Payload(testAddress, testLength);
  if (command == 1) testResult = activatePreset(testSlot);
  if (command == 2) { migratePresetLayout(); testResult = 0; }
  if (command == 3) { processCommand(testText); testResult = 0; }
  if (command == 4) { applyBootPresetIfActive(); testResult = 0; }
  if (command == 5) { initPresetButtons(); testResult = 0; }
  if (command == 6) { scanPresetButtons(); testResult = 0; }
  testCommand = 255;
  testDone++;
}
''', encoding="utf-8")

cases = []
wave_sets = [
    (Awp1221WaveRecipe(0, 31, 3, 127, (256,), 256),),
    (Awp1221WaveRecipe(0),),
    (Awp1221WaveRecipe(0), Awp1221WaveRecipe(0, 31, 3, 127, (256,), 256)),
    (Awp1221WaveRecipe(5, times=(5, 7, 9, 11), repeat=256),),
]
wave_sets += [
    (Awp1221WaveRecipe(4, times=(4, 7, 9)),),
    (Awp1221WaveRecipe(4, times=(4, 7, 9)), Awp1221WaveRecipe(4, times=(8, 10, 11))),
    (Awp1221WaveRecipe(5, times=(4, 7, 9, 11), repeat=256), Awp1221WaveRecipe(5, times=(8, 10, 11, 13))),
    (Awp1221WaveRecipe(0, 10, 2, 7, (256,), 10), Awp1221WaveRecipe(5, times=(4, 7, 9, 11), repeat=2)),
]
for waves in wave_sets:
    preset = model(*waves)
    payload = pack_awp1221_payload(preset)
    cases.append(dict(raw=pack_v2_preset(V2Preset(4, 0x1221, 0x50, payload)).hex(), payload=payload.hex(),
                      descriptors=awp1221_descriptor_words(preset), starts=allocate_awp1221_slices(waves),
                      thresholds=preset.thresholds, refs=preset.wave_refs, repeats=preset.event_repeats,
                      zero=preset.zero_time, press=preset.press_return, release=preset.release_return))
legacy3 = pack_v2_preset(V2Preset(3, 0x192A, 0x44, pack_awp192x_button_payload(
    Awp192xButtonPayloadV2(2, 0, 2, 1, 300, 100, 0, 0x0221, 0, 0, 0, 0, 0, 0, 0,
                           (Awp192xSineWaveformRecipe(0, 128, 64, 0, 360, 40000, 0),)))))
registers = [0] * 14
registers[12] = 0x2100
legacy5 = pack_v2_preset(V2Preset(5, 0x1018, 0x48, pack_awp1018_adc_stage4_payload(
    Awp1018AdcPresetModel(2, tuple(registers), (AWP1018WaveformDescriptor(0, (0x800, 0, 0, 0)),), False))))
legacy_type4 = []
for kind in (1, 2, 3):
    payload = bytearray(pack_awp1221_payload(model(Awp1221WaveRecipe(0, 31, 3, 127, (256,), 256))))
    del payload[21]
    payload[0] = 1
    payload[21] = kind
    legacy_type4.append(dict(kind=kind, raw=pack_v2_preset(V2Preset(4, 0x1221, 0x50, bytes(payload))).hex()))
(OUT / "avr_cases.json").write_text(json.dumps(dict(cases=cases, legacyType4=legacy_type4,
                                                  legacy=[legacy3.hex(), legacy5.hex()],
                                                  legacyCrc=[crc16_ccitt_false(legacy3), crc16_ccitt_false(legacy5)])), encoding="utf-8")
print(f"Prepared {len(cases)} Type-4 cases and two legacy records")
