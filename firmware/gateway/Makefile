CONTIKI_PROJECT = ec-gateway
all: $(CONTIKI_PROJECT)
CONTIKI ?= ../../vendor/contiki-ng
TARGET = simplelink
BOARD = launchpad/codm-cc2652p
MAKE_NET = MAKE_NET_NULLNET
MAKE_MAC = MAKE_MAC_OTHER
PROJECT_SOURCEFILES += ec-mac.c ec-protocol.c ec-peers.c ec-phase.c
UPDATE_SUPPORT ?= 0
ifeq ($(UPDATE_SUPPORT),1)
KEY_DIR ?= ../../.local/firmware-keys
PROJECTDIRS += ../update ../update/third_party/monocypher
PROJECT_SOURCEFILES += ec-update.c ec-update-platform.c ec-update-uart.c monocypher.c monocypher-ed25519.c
CFLAGS += -DEC_ENABLE_UPDATES=1 -I$(KEY_DIR)
LDSCRIPT = application.lds
OBJCOPY_FLAGS += --remove-section=.ccfg
endif
# The local uart0-arch.c overrides the CPU file through Contiki's project-first vpath.
OBJCOPY_FLAGS += --gap-fill=0xff
CFLAGS += -include toolchain-compat.h
CFLAGS += $(VERSION_FLAGS)
include $(CONTIKI)/Makefile.include
