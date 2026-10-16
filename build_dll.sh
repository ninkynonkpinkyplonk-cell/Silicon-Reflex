#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
case "$(uname -s):$(uname -m)" in
  Linux:x86_64) ;;
  *) echo 'This build target supports Linux x86_64 only.' >&2; exit 1 ;;
esac
"${CC:-cc}" -O3 -std=c11 -D_POSIX_C_SOURCE=200809L -Wall -Wextra -fPIC -shared \
  -o libdecision_core.so decision_core.c
