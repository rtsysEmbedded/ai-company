# Entwicklungsumgebung einrichten

## Toolchain
Installiere die ARM GNU Toolchain `arm-none-eabi-gcc` Version 14 und CMake 3.30.
Der Debugger ist ein ST-LINK V3 mit OpenOCD.

## Flashen
Mit `openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c "program build/app.elf verify reset exit"`
wird die Firmware geflasht und verifiziert.
