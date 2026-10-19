"""Prepare an offline sketch that exercises the current product command handlers."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/button_mode_20261019"
sketch = OUT / "I2cSpeedValidation"
sketch.mkdir(parents=True, exist_ok=True)
for source in (ROOT / "outputs/Without_GUI").iterdir():
    if source.suffix in (".cpp", ".h"):
        shutil.copy2(source, sketch / source.name)
(sketch / "I2cSpeedValidation.ino").write_text('''#include "protocol.h"
#include "i2c.h"
#include <Wire.h>
extern "C" {
  #include <utility/twi.h>
}
volatile uint8_t testCommand = 255, testDone = 0, testReady = 0;
char testText[36];
void setup() {
  initI2cBus();
  testReady = 42;
}
void loop() {
  uint8_t command = testCommand;
  if (command == 255) return;
  if (command == 0) processCommand(testText);
  if (command == 1) initI2cBus();
  if (command == 2) twi_handleTimeout(true);
  testCommand = 255;
  testDone++;
}
''', encoding="utf-8")
print(sketch)
