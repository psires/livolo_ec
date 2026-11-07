CC ?= cc
BUILD ?= build
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined

.PHONY: test clean
test: $(BUILD)/test_transport
	$(BUILD)/test_transport

$(BUILD)/test_transport: transport.c transport.h test_transport.c ../version.h
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) transport.c test_transport.c -o $@

clean:
	rm -rf $(BUILD)
