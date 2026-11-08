CC ?= cc
BUILD ?= build
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined

.PHONY: test clean
test: $(BUILD)/test_adapter $(BUILD)/test_health $(BUILD)/test_reject $(BUILD)/test_output
	$(BUILD)/test_adapter
	$(BUILD)/test_health
	$(BUILD)/test_reject
	$(BUILD)/test_output

$(BUILD)/test_health: ../health.c ../health.h test_health.c Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -I.. ../health.c test_health.c -o $@

$(BUILD)/test_reject: ../reject.c ../reject.h ../health.c ../health.h ../../experimental/rf.c ../../experimental/rf.h ../../experimental/transport.c ../../experimental/transport.h ../../version.h test_reject.c Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -DEC_RF_REJECT_DIAGNOSTICS=1 -DEC_MG24_REJECT_TEST=1 \
	 -I.. -I../../experimental ../reject.c ../health.c ../../experimental/rf.c \
	 ../../experimental/transport.c test_reject.c -o $@

clean:
	rm -rf $(BUILD)

$(BUILD)/test_output: ../main.c ../../experimental/transport.c ../../experimental/transport.h ../../experimental/rf.c ../../experimental/rf.h ../../version.h test_output.c $(wildcard stubs/*.h stubs/api/*.h) Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -DEC_RF_DIAGNOSTICS=1 -Istubs -I../../experimental \
	 ../../experimental/transport.c ../../experimental/rf.c test_output.c -o $@

$(BUILD)/test_adapter: test_adapter.c ../radio.c ../adapter.c ../adapter.h ../health.c ../health.h ../../experimental/rf.h $(wildcard adapter_stubs/*.h) Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -Iadapter_stubs -I.. -I../../experimental test_adapter.c ../health.c -o $@
