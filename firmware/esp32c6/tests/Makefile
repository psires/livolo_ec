CC ?= cc
BUILD ?= build
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined
SOURCES = ../main/main.c ../../experimental/transport.c test_usb_transport.c
HEADERS = $(wildcard stubs/*.h stubs/*/*.h) ../../experimental/transport.h ../../version.h

.PHONY: test clean
test: $(BUILD)/test_usb_transport $(BUILD)/test_rf_usb
	$(BUILD)/test_usb_transport
	$(BUILD)/test_rf_usb

$(BUILD)/test_usb_transport: $(SOURCES) $(HEADERS) Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -Istubs -I../../experimental -I../.. $(SOURCES) -o $@

$(BUILD)/test_rf_usb: ../main/main.c ../../experimental/transport.c ../../experimental/rf.c ../../experimental/rf.h test_rf_usb.c $(HEADERS) Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -DEC_RF_DIAGNOSTICS=1 -Istubs -I../../experimental -I../.. \
	 ../main/main.c ../../experimental/transport.c ../../experimental/rf.c test_rf_usb.c -o $@

clean:
	rm -rf $(BUILD)
