CONTIKI ?= ../../vendor/contiki-ng
SDK = $(CONTIKI)/arch/cpu/simplelink-cc13xx-cc26xx/lib/coresdk_cc13xx_cc26xx
DEV = $(SDK)/source/ti/devices/cc13x2_cc26x2
KEY_DIR ?= ../../.local/firmware-keys
CC = arm-none-eabi-gcc
CFLAGS = -std=c99 -Os -mcpu=cortex-m4 -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16 -ffunction-sections -fdata-sections -fno-common -DDeviceFamily_CC13X2 -I$(SDK)/source -I$(KEY_DIR) -I../update
SRC = main.c ../update/ec-update.c ../update/ec-update-platform.c ../update/third_party/monocypher/monocypher.c ../update/third_party/monocypher/monocypher-ed25519.c
all: build/ec-boot.bin
build/ec-boot.elf: $(SRC) bootloader.lds $(KEY_DIR)/update-public-key.h
	mkdir -p build
	$(CC) $(CFLAGS) $(SRC) -nostartfiles --specs=nano.specs --specs=nosys.specs -Tbootloader.lds -Wl,--gc-sections,-Map=build/ec-boot.map -Wl,--start-group $(DEV)/driverlib/bin/gcc/driverlib.lib -lc -lgcc -Wl,--end-group -o $@
	arm-none-eabi-size $@
build/ec-boot.bin: build/ec-boot.elf
	arm-none-eabi-objcopy -O binary --gap-fill=0xff $< $@
