CONTIKI_PROJECT = ec-receiver
all: $(CONTIKI_PROJECT)
CONTIKI ?= ../../vendor/contiki-ng
TARGET = simplelink
BOARD = launchpad/codm-cc2652p
MAKE_NET = MAKE_NET_NULLNET
MAKE_MAC = MAKE_MAC_OTHER
PROJECT_SOURCEFILES += ec-sniffer-mac.c
OBJCOPY_FLAGS += --gap-fill=0xff
CFLAGS += -include toolchain-compat.h
ifeq ($(BOOT_DIAGNOSTICS),1)
PROJECT_SOURCEFILES += boot-diagnostics.c
LDFLAGS += -Wl,--wrap=NOROM_SetupTrimDevice
LDFLAGS += -Wl,--wrap=platform_init_stage_one
LDFLAGS += -Wl,--wrap=platform_init_stage_two
LDFLAGS += -Wl,--wrap=Power_init -Wl,--wrap=PIN_init -Wl,--wrap=NoRTOS_start
endif
include $(CONTIKI)/Makefile.include
