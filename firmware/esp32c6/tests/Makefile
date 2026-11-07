CC ?= cc
BUILD ?= build
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined
SOURCES = ../main/main.c ../../experimental/transport.c test_usb_transport.c
HEADERS = $(wildcard stubs/*.h stubs/*/*.h) ../../experimental/transport.h ../../version.h

.PHONY: test clean
test: $(BUILD)/test_usb_transport
	$(BUILD)/test_usb_transport

$(BUILD)/test_usb_transport: $(SOURCES) $(HEADERS) Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -Istubs -I../../experimental -I../.. $(SOURCES) -o $@

clean:
	rm -rf $(BUILD)
