# Pinned dependencies are listed in README.md; no bootloader is built here.
DEPS ?= ../../vendor/nrf-port
TUSB = $(DEPS)/tinyusb
NRFX = $(DEPS)/nrfx-1.8.6
RADIO = $(DEPS)/radio-driver/src
PREFIX ?= arm-none-eabi-
CC = $(PREFIX)gcc
BUILD = build
USB_VID ?= 0xcafe
USB_PID ?= 0x4001
USB_RELEASE ?= 0
INCLUDES = . ../gateway $(TUSB)/src $(TUSB)/lib/CMSIS_5/CMSIS/Core/Include \
 $(NRFX) $(NRFX)/mdk $(NRFX)/hal $(NRFX)/drivers/include $(NRFX)/drivers/src \
 $(TUSB)/hw/bsp/nrf $(RADIO) $(RADIO)/fem/none
CFLAGS = -Os -g3 -std=c11 -mcpu=cortex-m4 -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16 \
 -ffunction-sections -fdata-sections -Wall -Wextra \
 -DNRF52840_XXAA -DEC_PLATFORM_NRF52840 -DEC_UART_BAUD=460800 \
 -DEC_USB_VID=$(USB_VID) -DEC_USB_PID=$(USB_PID) -DEC_USB_RELEASE=$(USB_RELEASE) \
 -DEC_AUTO_ACK_PHASE=1 -DEC_ENABLE_SCHEDULER=1 -DEC_SCHED_HZ=1000000 \
 -DNRF_802154_PROJECT_CONFIG=\"radio-config.h\" -DENABLE_FEM=0 \
 -D__START=main -D__STARTUP_CLEAR_BSS -D__STACK_SIZE=8192 -D__HEAP_SIZE=8192 \
 $(addprefix -I,$(INCLUDES))
RADIO_SOURCES = nrf_802154.c nrf_802154_core.c nrf_802154_core_hooks.c \
 nrf_802154_critical_section.c nrf_802154_debug.c nrf_802154_pib.c \
 nrf_802154_rssi.c nrf_802154_rx_buffer.c nrf_802154_timer_coord.c \
 nrf_802154_notification_direct.c nrf_802154_request_direct.c \
 nrf_802154_priority_drop_direct.c nrf_802154_swi.c fal/nrf_802154_fal.c \
 rsch/nrf_802154_rsch.c rsch/nrf_802154_rsch_crit_sect.c \
 rsch/raal/single_phy/single_phy.c timer_scheduler/nrf_802154_timer_sched.c \
 mac_features/nrf_802154_filter.c mac_features/nrf_802154_frame_parser.c \
 mac_features/ack_generator/nrf_802154_ack_generator.c \
 mac_features/ack_generator/nrf_802154_imm_ack_generator.c \
 mac_features/ack_generator/nrf_802154_enh_ack_generator.c \
 mac_features/ack_generator/nrf_802154_ack_data.c \
 platform/irq/nrf_802154_irq_baremetal.c platform/lp_timer/nrf_802154_lp_timer.c \
 platform/hp_timer/nrf_802154_hp_timer.c platform/coex/nrf_802154_wifi_coex_none.c \
 platform/temperature/nrf_802154_temperature_none.c platform/random/nrf_802154_random_stdlib.c
SOURCES = main.c usb-descriptors.c ec-clock.c ec-scheduler-nrf.c \
 ../gateway/ec-gateway.c ../gateway/ec-protocol.c ../gateway/ec-peers.c \
 ../gateway/ec-phase.c ../gateway/ec-scheduler.c \
 $(addprefix $(RADIO)/,$(RADIO_SOURCES)) \
 $(NRFX)/drivers/src/nrfx_power.c $(NRFX)/soc/nrfx_atomic.c \
 $(NRFX)/mdk/system_nrf52840.c \
 $(TUSB)/src/tusb.c $(TUSB)/src/common/tusb_fifo.c \
 $(TUSB)/src/device/usbd.c $(TUSB)/src/device/usbd_control.c \
 $(TUSB)/src/class/cdc/cdc_device.c $(TUSB)/src/portable/nordic/nrf5x/dcd_nrf5x.c
OBJECTS = $(addprefix $(BUILD)/,$(notdir $(SOURCES:.c=.o))) $(BUILD)/startup.o
vpath %.c $(sort $(dir $(SOURCES)))
all: $(BUILD)/ec-nrf52840.hex $(BUILD)/ec-nrf52840.bin
$(BUILD):
	mkdir -p $@
$(BUILD)/.usb-$(USB_VID)-$(USB_PID)-$(USB_RELEASE): | $(BUILD)
	rm -f $(BUILD)/.usb-*
	touch $@
$(OBJECTS): Makefile $(BUILD)/.usb-$(USB_VID)-$(USB_PID)-$(USB_RELEASE)
$(BUILD)/%.o: %.c | $(BUILD)
	$(CC) $(CFLAGS) -MMD -MP -c $< -o $@
$(BUILD)/startup.o: $(NRFX)/mdk/gcc_startup_nrf52840.S | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@
$(BUILD)/ec-nrf52840.elf: $(OBJECTS) application.ld
	$(CC) $(CFLAGS) -nostartfiles --specs=nano.specs --specs=nosys.specs \
	 -L$(NRFX)/mdk -T application.ld -Wl,--gc-sections,-Map=$(BUILD)/ec-nrf52840.map \
	 $(OBJECTS) -lm -o $@
	$(PREFIX)size $@
$(BUILD)/ec-nrf52840.hex: $(BUILD)/ec-nrf52840.elf
	$(PREFIX)objcopy -O ihex $< $@
$(BUILD)/ec-nrf52840.bin: $(BUILD)/ec-nrf52840.elf
	$(PREFIX)objcopy -O binary --gap-fill=0xff $< $@
clean:
	rm -rf $(BUILD)
-include $(OBJECTS:.o=.d)
.PHONY: all clean
