# Contributing

This project contains experimental radio firmware and a host daemon. Keep
documentation and formatting changes separate from behavior changes so reviewers
can assess protocol and timing changes independently.

## Source conventions

Project-owned C follows the [Linux kernel coding style](https://www.kernel.org/doc/html/latest/process/coding-style.html):
eight-column tabs, function braces on the following line, control-statement
braces on the same line, and one statement per line. Prefer lines within 80
columns, descriptive function names and short, focused helpers. Retain standard
fixed-width integer types and SDK-required names. Existing public type aliases
remain compatible with their callers. Do not reformat vendored Monocypher.

The root `.clang-format` captures the mechanical layout. It does not replace
review: preserve Contiki protothread semantics and SDK include macros. A guarded
`clang-format off` region must explain why it is required.

Document every named function, including private helpers, callbacks and tests.
For C, place a Doxygen block immediately above the definition or its public
declaration. State the function's purpose, every parameter's meaning and units,
buffer lengths/ownership where relevant, and the return or error contract.
Explain intent and constraints rather than narrating the statements in the body.

```c
/**
 * @brief Decide whether a received report belongs to the configured peer.
 * @param frame Complete MAC frame without FCS; borrowed for this call.
 * @param length Number of readable bytes in frame.
 * @return Nonzero for an accepted report, otherwise zero.
 */
```

Python uses Black with four-space indentation and an 88-column target. Put the
same purpose and `@param` contract in a function docstring; omit implicit `self`
and `cls`. A test's summary should state the behavior it protects. JavaScript
uses Prettier 3.3.3 with two-space indentation and JSDoc above named functions
and event-handler assignments; describe callback
parameters where the callback is not self-explanatory.

## Local checks

Use Python 3.9 or newer and a host C compiler. The pinned formatter versions keep
local results and CI consistent:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python tools/check_documentation.py
black --check tools host firmware
find firmware -path '*/third_party' -prune -o \( -name '*.c' -o -name '*.h' \) -exec clang-format --dry-run --Werror {} +
python tools/project_version.py
npx --yes prettier@3.3.3 --check host/livolod/static/app.js
node --check host/livolod/static/app.js
python -m unittest discover -s tools -p 'test_*.py'
PYTHONPATH=host python -m unittest discover -s host/tests -p 'test_*.py'
python -m unittest discover -s firmware/gateway/tests -p 'test_*.py'
python -m unittest discover -s firmware/update/tests -p 'test_*.py'
git diff --check
```

The JavaScript checks require Node.js and npm (`npx`).

To apply layout fixes, run `black tools host firmware` and replace the
`clang-format --dry-run --Werror` options with `clang-format -i`. Review the diff
before committing. Use `npx --yes prettier@3.3.3 --write host/livolod/static/app.js`
for JavaScript. The Python documentation check verifies presence and parameter
coverage; reviewers remain responsible for accuracy and for C/JavaScript docs.

The tests compile firmware cores against host stubs and use simulated radios,
local sockets and pseudo-terminals. They do not establish hardware timing or
validate a complete target firmware image. Hardware scripts require an explicitly
selected board and may transmit or flash; do not run them as ordinary unit tests.
See [gateway build instructions](firmware/gateway/README.md) for target builds.

## Publication scope

Keep signing keys, local configuration, databases and generated firmware outside
Git. Monocypher's license and upstream provenance travel with its source.
The project owner must select a license for the project-owned code before a
public open-source release; the third-party license does not license this project.

Development captures and logs are experimental evidence, not required release
artifacts. Only the development captures used by offline regression tests are
included in the source import. Historical reports may refer to additional local
evidence that is not distributed with the source.
