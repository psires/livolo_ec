CC ?= cc
BUILD ?= build
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined
SOURCES = ../main/main.c ../../experimental/transport.c test_usb_transport.c
HEADERS = $(wildcard stubs/*.h stubs/*/*.h) ../../experimental/transport.h ../../version.h

.PHONY: test clean
test: $(BUILD)/test_adapter $(BUILD)/test_usb_transport $(BUILD)/test_rf_usb $(BUILD)/test_output_scheduler
	$(BUILD)/test_adapter
	$(BUILD)/test_usb_transport
	$(BUILD)/test_rf_usb
	$(BUILD)/test_output_scheduler

$(BUILD)/test_usb_transport: $(SOURCES) $(HEADERS) Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -Istubs -I../../experimental -I../.. $(SOURCES) -o $@

$(BUILD)/test_rf_usb: ../main/main.c ../main/health.c ../main/health.h ../../experimental/transport.c ../../experimental/rf.c ../../experimental/rf.h test_rf_usb.c $(HEADERS) Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -DEC_RF_DIAGNOSTICS=1 -Istubs -I../main -I../../experimental -I../.. \
	 ../main/main.c ../main/health.c ../../experimental/transport.c ../../experimental/rf.c test_rf_usb.c -o $@

clean:
	rm -rf $(BUILD)

$(BUILD)/test_output_scheduler: ../main/main.c ../main/health.c ../main/health.h ../../experimental/transport.c ../../experimental/rf.c ../../experimental/rf.h test_output_scheduler.c $(HEADERS) Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -DEC_RF_DIAGNOSTICS=1 -Istubs -I../main -I../../experimental -I../.. \
	 ../main/main.c ../main/health.c ../../experimental/transport.c ../../experimental/rf.c test_output_scheduler.c -o $@

$(BUILD)/test_adapter: test_adapter.c ../main/radio.c ../main/adapter.c ../main/adapter.h ../main/health.h ../../experimental/rf.h $(wildcard adapter_stubs/*.h adapter_stubs/*/*.h) Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -Iadapter_stubs -I../main -I../../experimental test_adapter.c -o $@
