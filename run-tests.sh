#!/usr/bin/env bash
# Everything CI would run. No arguments, no hardware needed.
set -euo pipefail
cd "$(dirname "$0")"

echo "--- syntax ---"
for f in inhibit-idle-while-watching/*.luau; do
  if luau-compile "$f" >/dev/null; then
    echo "ok    $f"
  else
    echo "FAIL  $f"
    exit 1
  fi
done
python3 -m py_compile inhibit-idle-while-watching/face-watch
echo "ok    inhibit-idle-while-watching/face-watch (py_compile)"

echo
echo "--- lint ---"
# service.luau is excluded deliberately: onIpc/onConfigChanged/onExit are
# called by the Noctalia host, so luau-analyze reports them as unused.
luau-analyze inhibit-idle-while-watching/policy.luau

echo
echo "--- manifest ---"
# parseFieldType() in the host returns String for any type it does not
# recognise, so a typo in plugin.toml quietly turns a control into a text box.
if command -v noctalia >/dev/null 2>&1; then
  noctalia plugins lint inhibit-idle-while-watching | sed 's/^/      /'
else
  echo "      skip: noctalia not on PATH"
fi

echo
echo "--- tests ---"
luau tests/policy.test.luau
python3 tests/test_face_watch.py
