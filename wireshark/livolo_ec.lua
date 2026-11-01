-- Livolo EC over IEEE 802.15.4. See analysis/protocol.md for evidence/limits.
-- Load with -X lua_script:wireshark/livolo_ec.lua or the personal plugins folder.
-- All offsets below start at the MAC payload, not at the capture/TAP header.

local ec = Proto("livolo_ec", "Livolo EC")
local f = {}
local opcodes = {
    [0x01] = "Registration candidate", [0x02] = "Set outputs",
    [0x05] = "State report", [0x07] = "Announcement",
    [0x08] = "Heartbeat / keepalive", [0x09] = "Announcement variant",
    [0x0a] = "Poll candidate", [0x0b] = "Unobserved request (response seen)",
    [0x0c] = "Discovery candidate", [0x10] = "Query 10",
    [0x11] = "Maintenance candidate", [0x15] = "Property report",
    [0xa0] = "Capabilities candidate", [0xac] = "Query AC",
    [0xfa] = "Acknowledgment / response",
}

f.source = ProtoField.string("livolo_ec.source", "Immediate source identity (wire order)")
f.destination = ProtoField.string("livolo_ec.destination", "Destination identity (wire order)")
f.route_a = ProtoField.bytes("livolo_ec.route_a", "Routing slot A (meaning partly unknown)")
f.route_b = ProtoField.bytes("livolo_ec.route_b", "Routing slot B (meaning partly unknown)")
f.tag = ProtoField.uint8("livolo_ec.tag", "Domain/network tag candidate", base.HEX)
f.flags = ProtoField.uint8("livolo_ec.flags", "Routing/delivery flags (bits unresolved)", base.HEX)
f.family = ProtoField.uint8("livolo_ec.family", "Application family", base.HEX)
f.opcode = ProtoField.uint8("livolo_ec.opcode", "Opcode", base.HEX, opcodes)
f.operation = ProtoField.string("livolo_ec.operation", "Operation")
f.subject = ProtoField.bytes("livolo_ec.subject", "Subject identity (wire order)")
f.suffix = ProtoField.bytes("livolo_ec.suffix", "Subject suffix/class (not a proven gang count)")
f.body = ProtoField.bytes("livolo_ec.body", "Checksummed application body")
f.data = ProtoField.bytes("livolo_ec.data", "Operation data")
f.mask = ProtoField.uint8("livolo_ec.mask", "Selected/affected output mask", base.HEX)
f.state = ProtoField.uint8("livolo_ec.state", "Desired/reported state bits", base.HEX)
f.output0 = ProtoField.bool("livolo_ec.state.bit0", "Output bit 0", 8, nil, 0x01)
f.output1 = ProtoField.bool("livolo_ec.state.bit1", "Output bit 1", 8, nil, 0x02)
f.counter = ProtoField.uint8("livolo_ec.counter", "Report/event counter", base.DEC)
f.token = ProtoField.uint8("livolo_ec.token", "Transaction token (may be stale)", base.HEX)
f.ack_opcode = ProtoField.uint8("livolo_ec.ack_opcode", "Acknowledged/responded-to opcode", base.HEX, opcodes)
f.ack_data = ProtoField.bytes("livolo_ec.ack_data", "Echoed data / response body")
f.property_key = ProtoField.uint8("livolo_ec.property.key", "Property key (meaning unresolved)", base.HEX)
f.property_value = ProtoField.uint8("livolo_ec.property.value", "Property value", base.DEC_HEX)
f.heartbeat_counter = ProtoField.uint32("livolo_ec.heartbeat.counter", "Heartbeat counter (seconds candidate)", base.DEC)
f.heartbeat_parameter = ProtoField.uint32("livolo_ec.heartbeat.parameter", "Heartbeat parameter (meaning unresolved)", base.DEC)
f.sum8 = ProtoField.uint8("livolo_ec.sum8", "Application additive checksum", base.HEX)
f.crc16 = ProtoField.uint16("livolo_ec.crc16", "Application CRC-16/CCITT-FALSE", base.HEX)
f.calculated = ProtoField.uint16("livolo_ec.checksum.calculated", "Calculated application checksum", base.HEX)
f.checksum_good = ProtoField.bool("livolo_ec.checksum.good", "Application checksum valid")
f.trailer = ProtoField.bytes("livolo_ec.trailer", "Timing trailer (rewritten by relays)")
f.phase_fraction = ProtoField.uint16("livolo_ec.phase.fraction", "Phase fraction (65500 units/second model)", base.DEC)
f.phase_seconds = ProtoField.uint8("livolo_ec.phase.seconds", "Phase whole seconds", base.DEC)
f.phase_age = ProtoField.double("livolo_ec.phase.age", "Modeled heartbeat-relative age (seconds)")
ec.fields = f

local bad_checksum = ProtoExpert.new("livolo_ec.checksum.bad", "Invalid EC application checksum",
    expert.group.CHECKSUM, expert.severity.WARN)
local truncated = ProtoExpert.new("livolo_ec.truncated", "Truncated EC payload",
    expert.group.MALFORMED, expert.severity.ERROR)
ec.experts = {bad_checksum, truncated}

local mac_fcf = Field.new("wpan.fcf")
local src_pan, src_short = Field.new("wpan.src_pan"), Field.new("wpan.src16")
local dst_pan, dst_short = Field.new("wpan.dst_pan"), Field.new("wpan.dst16")
local bitops = bit32 or bit

--- Return lowercase contiguous wire-order hex for a range.
-- @param range Borrowed TvbRange; never retained after dissection.
-- @return Hex string with two digits per byte.
local function hex(range)
    return range:bytes():tohex(false, ""):lower()
end

--- Reconstruct an EC identity from two MAC fields without endian reversal.
-- @param pan PAN field extractor.
-- @param address Short address field extractor.
-- @return Wire-order identity string, or nil when unavailable.
local function identity(pan, address)
    local p, a = pan(), address()
    if p and a then return hex(p.range) .. hex(a.range) end
end

--- Check the documented MAC layout and payload length before claiming traffic.
-- @param tvb MAC payload supplied by Wireshark (FCS already handled).
-- @return Expected payload size, or nil for unrelated/unsupported traffic.
local function expected_size(tvb)
    local control = mac_fcf()
    if not control or control.value ~= 0x8801 or tvb:len() < 12 then return nil end
    local family = tvb(10, 1):uint()
    local size = ({[0x8c] = 30, [0x9e] = 28})[family]
    if not size or tvb:reported_len() ~= size then return nil end
    return size
end

--- Calculate the family-selected application checksum over exactly 16 bytes.
-- @param tvb Complete MAC payload.
-- @param family Numeric family (0x8c or 0x9e).
-- @return Unsigned additive checksum or unreflected CCITT-FALSE CRC.
local function checksum(tvb, family)
    local value = family == 0x8c and 0 or 0xffff
    for offset = 10, 25 do
        local byte = tvb(offset, 1):uint()
        if family == 0x8c then
            value = (value + byte) % 256
        else
            value = bitops.bxor(value, byte * 256)
            for _ = 1, 8 do
                local high = value >= 0x8000
                value = (value * 2) % 65536
                if high then value = bitops.bxor(value, 0x1021) end
            end
        end
    end
    return value
end

--- Resolve family-sensitive operation names without inferring command causality.
-- @param tvb Complete MAC payload.
-- @param family Numeric application family.
-- @param opcode Numeric opcode.
-- @return Human-readable operation with uncertain semantics labeled.
local function operation(tvb, family, opcode)
    if opcode == 0x08 then
        if family == 0x9e then return "Device keepalive candidate" end
        if hex(tvb(12, 6)) == "000000000000" then return "Gateway heartbeat" end
        return "Keepalive response candidate"
    end
    if (opcode == 0x02 and family ~= 0x8c)
        or (opcode == 0x05 and family ~= 0x9e) then
        return string.format("Unknown family/opcode %02x/%02x", family, opcode)
    end
    return opcodes[opcode] or string.format("Unknown opcode 0x%02x", opcode)
end

--- Decode one recognized payload; stateless for safe GUI redissection/two-pass use.
-- @param tvb Borrowed MAC payload Tvb.
-- @param pinfo Packet metadata and writable summary columns.
-- @param tree Packet tree receiving EC fields.
-- @return Consumed captured bytes, or zero when not a documented EC layout.
function ec.dissector(tvb, pinfo, tree)
    local size = expected_size(tvb)
    if not size then return 0 end
    local root = tree:add(ec, tvb(), "Livolo EC")
    pinfo.cols.protocol = "Livolo EC"
    if tvb:len() < size then
        pinfo.cols.info = "Truncated Livolo EC payload"
        root:add_proto_expert_info(truncated)
        return tvb:len()
    end

    local family, opcode = tvb(10, 1):uint(), tvb(11, 1):uint()
    local name = operation(tvb, family, opcode)
    local source, destination = identity(src_pan, src_short), identity(dst_pan, dst_short)
    if source then root:add(f.source, source):set_generated() end
    if destination then root:add(f.destination, destination):set_generated() end
    root:add(f.route_a, tvb(0, 4))
    root:add(f.route_b, tvb(4, 4))
    root:add(f.tag, tvb(8, 1))
    root:add(f.flags, tvb(9, 1))
    local body = root:add(f.body, tvb(10, 16))
    body:add(f.family, tvb(10, 1))
    body:add(f.opcode, tvb(11, 1))
    body:add(f.operation, name):set_generated()
    body:add(f.subject, tvb(12, 4))
    body:add(f.suffix, tvb(16, 2))
    local data = body:add(f.data, tvb(18, 8))
    local info = string.format("%s | %s -> %s | subject=%s tag=%02x", name,
        source or "?", destination or "?", hex(tvb(12, 4)), tvb(8, 1):uint())

    if (family == 0x8c and opcode == 0x02) or (family == 0x9e and opcode == 0x05) then
        data:add(f.mask, tvb(20, 1))
        local state = data:add(f.state, tvb(21, 1))
        state:add(f.output0, tvb(21, 1))
        state:add(f.output1, tvb(21, 1))
        data:add(f.token, tvb(25, 1))
        if family == 0x9e then data:add(f.counter, tvb(24, 1)) end
        info = info .. string.format(" mask=%02x state=%02x token=%02x",
            tvb(20, 1):uint(), tvb(21, 1):uint(), tvb(25, 1):uint())
    elseif opcode == 0xfa then
        data:add(f.ack_opcode, tvb(18, 1))
        data:add(f.ack_data, tvb(19, 7))
        info = info .. string.format(" response-to=%02x", tvb(18, 1):uint())
        -- Only gateway-family ACKs are documented as seven-byte echoes.
        if family == 0x8c and tvb(18, 1):uint() == 0x05 then
            data:add(f.mask, tvb(21, 1))
            data:add(f.state, tvb(22, 1))
            data:add(f.counter, tvb(25, 1))
        end
    elseif family == 0x9e and opcode == 0x15 then
        data:add(f.property_key, tvb(19, 1))
        data:add(f.property_value, tvb(20, 1))
        data:add(f.counter, tvb(24, 1))
    elseif name == "Gateway heartbeat" then
        data:add_le(f.heartbeat_counter, tvb(18, 4))
        data:add_le(f.heartbeat_parameter, tvb(22, 4))
    end

    local calculated = checksum(tvb, family)
    local stored_range = tvb(26, family == 0x8c and 1 or 2)
    local stored = stored_range:uint()
    local check = root:add(family == 0x8c and f.sum8 or f.crc16, stored_range)
    check:add(f.calculated, calculated):set_generated()
    check:add(f.checksum_good, calculated == stored):set_generated()
    if calculated ~= stored then
        check:add_proto_expert_info(bad_checksum,
            string.format("Stored 0x%04x, calculated 0x%04x", stored, calculated))
        info = info .. " [BAD EC CHECKSUM]"
    end
    if family == 0x8c then
        local trailer = root:add(f.trailer, tvb(27, 3))
        trailer:add_le(f.phase_fraction, tvb(27, 2))
        trailer:add(f.phase_seconds, tvb(29, 1))
        trailer:add(f.phase_age, tvb(29, 1):uint() + tvb(27, 2):le_uint() / 65500):set_generated()
    end
    pinfo.cols.info = info
    return tvb:len()
end

--- Claim only documented EC framing, retaining bad checksums for diagnostics.
-- @param tvb MAC payload supplied by the wpan heuristic list.
-- @param pinfo Current packet metadata.
-- @param tree Packet tree.
-- @return True only when the EC dissector consumed this payload.
local function heuristic(tvb, pinfo, tree)
    return ec.dissector(tvb, pinfo, tree) > 0
end

ec:register_heuristic("wpan", heuristic)
DissectorTable.get("wpan.panid"):add_for_decode_as(ec)
