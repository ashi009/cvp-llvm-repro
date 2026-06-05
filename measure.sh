#!/bin/bash
# Reproduce the super-linear compile-time scaling.
# Usage: ./measure.sh [toolchain]   (default: current default toolchain)
set -euo pipefail
TC="${1:-}"
PLUS=""; [ -n "$TC" ] && PLUS="+$TC"
echo "toolchain: $(rustc $PLUS --version) / $(rustc $PLUS --version --verbose | grep -i LLVM)"
printf "%6s | %10s\n" "N" "wall(s)"
for N in 16 32 48 64 96 128 192 256; do
  python3 generate.py "$N" > /tmp/_repro.rs
  s=$(python3 -c 'import time;print(time.time())')
  rustc $PLUS -O -Ccodegen-units=1 --crate-type=lib --emit=obj -o /tmp/_repro.o /tmp/_repro.rs
  e=$(python3 -c 'import time;print(time.time())')
  printf "%6s | %10s\n" "$N" "$(python3 -c "print(f'{$e-$s:.2f}')")"
done
