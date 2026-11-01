# Livolo EC Wireshark dissector

[`livolo_ec.lua`](livolo_ec.lua) decodes the proprietary EC payload carried
directly in IEEE 802.15.4 data frames. It follows the current
[protocol reconstruction](../analysis/protocol.md), including the October 2026
keepalive, stale-token and forwarded-state observations. No compilation or
additional Lua packages are required. Tested with Wireshark/TShark **4.6.9** on
macOS; other Lua-enabled builds are not yet runtime-verified.

## Install

In Wireshark, open **About Wireshark → Folders** and find **Personal Lua Plugins**.
Copy `livolo_ec.lua` directly into that folder, then restart Wireshark. On this
Mac, Wireshark 4.6.9 reports `~/.local/lib/wireshark/plugins`:

```sh
mkdir -p "$HOME/.local/lib/wireshark/plugins"
cp wireshark/livolo_ec.lua "$HOME/.local/lib/wireshark/plugins/"
```

Run these commands from the repository root. Use the folder reported by your
installation on other systems; do not use the versioned binary-plugin folder.
Remove this one Lua file and restart to uninstall. Copy it again after changing
the repository version. Keep only one installed copy.

Open any capture under `captured/` and enter `livolo_ec` in the display filter.
Recognized packets have **Livolo EC** in the Protocol column and an expandable
**Livolo EC** tree. The normal IEEE 802.15.4 and capture metadata remain available.
Right-click a field and select **Apply as Column** to show identities, state,
token, timing or checksum validity in the packet list.

For a one-time run **without installing**:

```sh
/Applications/Wireshark.app/Contents/MacOS/tshark \
  -X lua_script:wireshark/livolo_ec.lua \
  -r captured/ec_set_on_off_on_off.pcapng -Y livolo_ec
```

After installation, omit `-X lua_script:...` to avoid loading a second copy.
On systems where TShark is on PATH, use `tshark` in place of the macOS path.

## Fields and filters

| Display filter | Selects |
| --- | --- |
| `livolo_ec` | All recognized EC traffic |
| `livolo_ec.family == 0x8c && livolo_ec.opcode == 0x02` | Output commands |
| `livolo_ec.family == 0x9e && livolo_ec.opcode == 0x05` | State reports, including forwarded reports |
| `livolo_ec.subject == 46:71:56:21` | Messages about the test switch |
| `livolo_ec.source == "75710834"` | Immediate transmitter in wire-order hex |
| `livolo_ec.tag == 0xb1` | Original gateway's observed domain tag |
| `livolo_ec.state.bit0 == true` | Bit 0 set in a command or state report |
| `livolo_ec.opcode == 0xfa && livolo_ec.ack_opcode == 0x05` | State acknowledgments |
| `livolo_ec.opcode == 0x10 || livolo_ec.opcode == 0xac` | Queries |
| `livolo_ec.operation == "Gateway heartbeat"` | Zero-subject gateway heartbeats |
| `livolo_ec.checksum.good == false` | Bad EC application checksum |
| `livolo_ec.truncated` | Recognizable payload cut short by capture snap length |

The plugin exposes routing slots, tag, flags, family/opcode, subject/suffix,
raw application body/data, output mask/state bits, report counter, token,
acknowledged opcode, property key/value, heartbeat counter/parameter and both
stored/calculated checksum values. `livolo_ec.phase.age` models the 8c timing
trailer as `seconds + fraction / 65500`, matching the current gateway encoder.
Raw fraction/seconds remain available; relay timing semantics are not fully known.

`source` and `destination` are generated strings combining the transmitted PAN
and short-address bytes. They are EC identities, not standard 32-bit MAC
addresses. `subject` and routing slots are byte fields. The source of a forwarded
state report can differ from its subject; the dissector preserves both.

Native reports may carry a stale token even after a successful command or a
physical press. No request/response or relay links are inferred from tokens or
sequence numbers. Registration, capabilities, flags, suffixes and property
meanings retain the documentation's uncertainty. Suffixes are not labeled as
gang counts. Higher state bits are preserved without assigning physical outputs.

For `8c/fa` acknowledgments of `05`, mask/state/counter are decoded from the
shifted seven-byte echo. The original report token was omitted on the wire, so
the ACK has no `livolo_ec.token`. Switch-family `9e/fa` query responses remain
raw except for the responded-to opcode; they are not assumed to be echoes.

## Recognition and capture framing

The `wpan` heuristic requires MAC FCF `0x8801`, family `8c` with a 30-byte
payload or `9e` with a 28-byte payload. These correspond to 41/39 MAC bytes
without FCS. Unknown opcodes inside these layouts remain visible. Unsupported
layouts are left to Wireshark's other dissectors.

Recognition deliberately does not require a valid application checksum, so
corrupted EC traffic remains inspectable and receives an Expert Information
warning. A coincidentally matching unrelated payload could therefore be claimed.
Frames truncated before the family/opcode or reporting an unsupported total
length cannot safely be recognized. Truncated recognized frames receive an
Expert Information error without reading past captured bytes.

Wireshark handles TAP headers (including variable-length TLVs), MAC headers and
MAC FCS before calling the plugin. The plugin never strips presumed CRC bytes
or assumes a fixed 28-byte TAP header. The additive checksum and big-endian
CRC-16/CCITT-FALSE each cover the 16-byte EC application body, independent of
the MAC FCS. TAP with no FCS, raw 802.15.4 without FCS, and raw 802.15.4 with
FCS are covered by the runtime tests. An invalid MAC FCS may prevent Wireshark
from offering a payload to the plugin under its normal IEEE 802.15.4 preferences.

If a profile forces another protocol for the PAN, remove that Decode As rule.
Alternatively choose **Livolo EC** for **IEEE 802.15.4 PANID** under Decode As.
For the original gateway's source PAN, the TShark equivalent is
`-d wpan.panid==0xf49b,livolo_ec`. The normal heuristic also decodes native
switches and relays without a hard-coded gateway address or PAN.

Integration references: Wireshark's
[Lua protocol API](https://www.wireshark.org/docs/wsdg_html_chunked/lua_module_Proto.html)
and [IEEE 802.15.4 dissector](https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-ieee802154.c).

## Validation

From the repository root:

```sh
python3 -m unittest discover -s wireshark -p 'test_*.py' -v
```

The suite discovers TShark on PATH or in `/Applications/Wireshark.app`.
Set `TSHARK=/absolute/path/to/tshark` to select another build. Missing TShark
produces an explicit skip, not a successful runtime validation. Tests isolate
personal plugins/preferences so they exercise the repository Lua file even
when a copy is installed.

Six integration tests check all 1,141 original packets against the independent
Python decoder, the 121-packet mixed capture (11 EC packets), corrupt checksums,
truncation, unsupported framing, both MAC FCS modes, Decode As, keepalive versus
heartbeat fields, property/ACK fields, and repeat dissection. The optional
October 25 live capture also checks every recognized frame and the documented
forwarded report at frame 1701; this test explicitly skips when that development
capture is absent. No test transmits radio traffic or changes devices.
