#!/usr/bin/env python3
# Generate a standalone repro: ONE function that builds a struct of N *droppable*
# fields, each initialized through a fallible `?` call. Every `?` early-return must
# drop the fields built so far -> O(N^2) cleanup blocks -> stresses CVP/LVI.
import sys
N = int(sys.argv[1])
out = []
out.append("// Auto-generated repro for rust-lang/rust#157463. See README.")
out.append("#![allow(dead_code)]")
out.append("")
# Opaque fallible producer of a droppable value (String has drop glue).
out.append("#[inline(never)]")
out.append("pub fn make_one(x: u64) -> Result<String, ()> {")
out.append("    if x == u64::MAX { Err(()) } else { Ok(x.to_string()) }")
out.append("}")
out.append("")
out.append("pub struct Big {")
for i in range(N):
    out.append(f"    f{i}: String,")
out.append("}")
out.append("")
out.append("#[inline(never)]")
out.append("pub fn build(seed: u64) -> Result<Big, ()> {")
out.append("    Ok(Big {")
for i in range(N):
    out.append(f"        f{i}: make_one(seed ^ {i})?,")
out.append("    })")
out.append("}")
print("\n".join(out))
