# Gecko SDK v4.4.6, commit 59d2160fe448b13730a91c0f019a8b4c53c43eb2.
GSDK ?= ../../vendor/gecko_sdk
PREFIX ?= arm-none-eabi-
CC = $(PREFIX)gcc
BUILD = build
UART_BAUD ?= 921600
BOARD ?= sonoff-zbdongle-e
ifeq ($(BOARD),zb-gw04-1v2)
BOARD_DEFINE = EC_BOARD_ZB_GW04_1V2
else ifeq ($(BOARD),sonoff-zbdongle-e)
BOARD_DEFINE = EC_BOARD_SONOFF_ZBDONGLE_E
else
$(error Unsupported BOARD=$(BOARD))
endif
DEVICE = $(GSDK)/platform/Device/SiliconLabs/EFR32MG21
EMLIB = $(GSDK)/platform/emlib
RAIL = $(GSDK)/platform/radio/rail_lib
PA = $(RAIL)/plugin/pa-conversions
RADIO_LIB = $(RAIL)/autogen/librail_release/librail_efr32xg21_gcc_release.a
INCLUDES = . ../mg21 ../gateway $(DEVICE)/Include $(GSDK)/platform/CMSIS/Core/Include \
 $(EMLIB)/inc $(GSDK)/platform/common/inc $(GSDK)/platform/bootloader \
 $(RAIL)/common $(RAIL)/protocol/ieee802154 $(RAIL)/chip/efr32/efr32xg2x \
 $(PA) $(PA)/efr32xg21/config
CFLAGS = -Os -g3 -std=c11 -mcpu=cortex-m33 -mthumb -mcmse -mfpu=fpv5-sp-d16 -mfloat-abi=hard \
 -ffunction-sections -fdata-sections -Wall -Wextra \
 -DEC_UART_BAUD=$(UART_BAUD) -D$(BOARD_DEFINE) -DEFR32MG21A020F768IM32 -DEC_PLATFORM_MG21 -DSL_APP_PROPERTIES \
 -DSL_LEGACY_LINKER -D__START=main -D__STACK_SIZE=8192 -D__HEAP_SIZE=0 \
 $(addprefix -I,$(INCLUDES))
SOURCES = main.c ../mg21/ec-uart-rx.c \
 $(DEVICE)/Source/startup_efr32mg21.c $(DEVICE)/Source/system_efr32mg21.c \
 $(addprefix $(EMLIB)/src/,em_cmu.c em_emu.c em_gpio.c em_usart.c em_ldma.c em_core.c em_msc.c em_system.c) \
 $(PA)/pa_conversions_efr32.c $(PA)/pa_curves_efr32.c
OBJECTS = $(addprefix $(BUILD)/,$(notdir $(SOURCES:.c=.o)))
vpath %.c $(sort $(dir $(SOURCES)))

all: $(BUILD)/ec-sniffer.bin
$(BUILD):
	mkdir -p $@
$(BUILD)/.config-$(BOARD)-$(UART_BAUD): | $(BUILD)
	rm -f $(BUILD)/.config-* $(BUILD)/.uart-baud-*
	touch $@
$(OBJECTS): $(BUILD)/.config-$(BOARD)-$(UART_BAUD) Makefile
$(BUILD)/%.o: %.c | $(BUILD)
	$(CC) $(CFLAGS) -MMD -MP -c $< -o $@
$(BUILD)/ec-sniffer.elf: $(OBJECTS) ../mg21/application.ld
	$(CC) $(CFLAGS) -nostartfiles --specs=nano.specs --specs=nosys.specs \
	 -T ../mg21/application.ld -Wl,--gc-sections,-Map=$(BUILD)/ec-sniffer.map \
	 $(OBJECTS) -Wl,--whole-archive $(RADIO_LIB) -Wl,--no-whole-archive -lm -o $@
	$(PREFIX)size $@
$(BUILD)/ec-sniffer.bin: $(BUILD)/ec-sniffer.elf
	$(PREFIX)objcopy -O binary --gap-fill=0xff $< $@
clean:
	rm -rf $(BUILD)
-include $(OBJECTS:.o=.d)
.PHONY: all clean
