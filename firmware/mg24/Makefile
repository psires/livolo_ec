# Experimental transport-only target. Separate pinned Gecko SDK 4.4.6 checkout.
GSDK ?= ../../vendor/gecko_sdk
PREFIX ?= arm-none-eabi-
CC = $(PREFIX)gcc
BUILD ?= build
DEVICE = $(GSDK)/platform/Device/SiliconLabs/EFR32MG24
EMLIB = $(GSDK)/platform/emlib
INCLUDES = ../experimental $(DEVICE)/Include $(GSDK)/platform/CMSIS/Core/Include \
 $(EMLIB)/inc $(GSDK)/platform/common/inc $(GSDK)/platform/bootloader
CFLAGS = -Os -g3 -std=c11 -mcpu=cortex-m33 -mthumb -mcmse \
 -mfpu=fpv5-sp-d16 -mfloat-abi=hard -ffunction-sections -fdata-sections \
 -Wall -Wextra -DEFR32MG24B220F1536IM48 -DSL_APP_PROPERTIES \
 -DSL_LEGACY_LINKER -D__START=main -D__STACK_SIZE=8192 -D__HEAP_SIZE=0 \
 $(addprefix -I,$(INCLUDES))
SOURCES = main.c ../experimental/transport.c \
 $(DEVICE)/Source/startup_efr32mg24.c $(DEVICE)/Source/system_efr32mg24.c \
 $(addprefix $(EMLIB)/src/,em_cmu.c em_emu.c em_gpio.c em_usart.c em_core.c em_system.c)
OBJECTS = $(addprefix $(BUILD)/,$(notdir $(SOURCES:.c=.o)))
vpath %.c $(sort $(dir $(SOURCES)))

.PHONY: all clean
all: $(BUILD)/ec-xiao-mg24.bin
$(BUILD):
	mkdir -p $@
$(BUILD)/%.o: %.c Makefile | $(BUILD)
	$(CC) $(CFLAGS) -MMD -MP -c $< -o $@
$(BUILD)/ec-xiao-mg24.elf: $(OBJECTS) application.ld
	$(CC) $(CFLAGS) -nostartfiles --specs=nano.specs --specs=nosys.specs \
	 -T application.ld -Wl,--gc-sections,-z,max-page-size=0x1000,-Map=$(BUILD)/ec-xiao-mg24.map \
	 $(OBJECTS) -lm -o $@
	$(PREFIX)size $@
$(BUILD)/ec-xiao-mg24.bin: $(BUILD)/ec-xiao-mg24.elf
	$(PREFIX)objcopy -O binary --gap-fill=0xff $< $@
clean:
	rm -rf $(BUILD)
-include $(OBJECTS:.o=.d)
