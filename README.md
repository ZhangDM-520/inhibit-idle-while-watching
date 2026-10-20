# inhibit-idle-while-watching

Noctalia plugin: presence-aware idle suppression. When Noctalia's idle chain is
about to act, capture one webcam frame; while a face keeps appearing, hold the
idle inhibitors (logind `idle` + niri `ScreenSaver.Inhibit`); with no face, let
the configured dim/screen-off/lock chain proceed untouched.

- `inhibit-idle-while-watching/` — the plugin (plugin.toml, service.luau,
  policy.luau, face-watch helper, translations, README).
- `tests/` — pure Luau policy tests + Python helper tests (no hardware needed);
  `run-tests.sh` runs everything CI would.
- `docs/DESIGN.md` — decisions and measured facts.

## Development

```sh
./run-tests.sh
```

Live install: copy `inhibit-idle-while-watching/` into
`~/.local/state/noctalia/plugins/materialized/community/` and enable it, then
add the idle-behaviour handshake from the plugin README.
