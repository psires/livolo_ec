CC ?= cc
BUILD ?= build
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined

.PHONY: test clean
test: $(BUILD)/test_transport $(BUILD)/test_rf
	$(BUILD)/test_transport
	$(BUILD)/test_rf

$(BUILD)/test_transport: transport.c transport.h test_transport.c ../version.h
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) transport.c test_transport.c -o $@

$(BUILD)/test_rf: rf.c rf.h test_rf.c Makefile
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) rf.c test_rf.c -o $@

clean:
	rm -rf $(BUILD)
