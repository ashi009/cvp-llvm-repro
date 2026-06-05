# Super-linear (~cubic) `-O` compile time for a `?`-chain initializing a struct of droppable fields

<!-- Suggested title for rust-lang/rust. Labels: I-compiletime, A-LLVM, A-codegen, A-mir-opt-inlining?, T-compiler, C-bug -->

### Summary

A single function that initializes `N` **droppable** struct fields, each via the `?`
operator, makes the LLVM function-optimization pipeline scale roughly **cubically** in
`N`. With `N = 256` the function takes **~70 s** to compile at `-O`; the same struct
built **without** `?` compiles in **0.2 s**.

This pattern shows up in real macro-generated code: I hit it in a derive macro that
emits a `new()` constructor with ~124 `?`-initialized fields, which took ~120 s to
compile a single crate. Reduced minimal reproducer below (no proc-macros, no
dependencies).

### Code

`make_one` is an opaque fallible producer of a droppable value; `build` initializes
`N` fields through `?`:

```rust
#[inline(never)]
pub fn make_one(x: u64) -> Result<String, ()> {
    if x == u64::MAX { Err(()) } else { Ok(x.to_string()) }
}

pub struct Big { f0: String, f1: String, /* ... */ f127: String }

#[inline(never)]
pub fn build(seed: u64) -> Result<Big, ()> {
    Ok(Big {
        f0:   make_one(seed ^ 0)?,
        f1:   make_one(seed ^ 1)?,
        // ... N total ...
        f127: make_one(seed ^ 127)?,
    })
}
```

Full reproducer + generator (`generate.py N`) + measurement scripts:
<https://github.com/OWNER/cvp-llvm-repro> *(fill in once pushed)*

### Steps to reproduce

```bash
python3 generate.py 128 > repro_128.rs
rustc -O -Ccodegen-units=1 --crate-type=lib --emit=obj -o /dev/null repro_128.rs
```

### Compile time vs N — `rustc 1.94.1`, `-O -Ccodegen-units=1`

| N | 16 | 32 | 48 | 64 | 96 | 128 | 192 | 256 |
|---|----|----|----|----|----|-----|-----|-----|
| wall | 0.1s | 0.2s | 0.5s | 1.0s | 3.2s | 8.1s | 23.8s | **69.2s** |

Each doubling of N (64→128→256) multiplies wall time by ~8× → **≈ O(N³)**.

### The `?` operator is the trigger

Same `N` droppable fields, built infallibly (`make_one` returns `String`, no `?`):

| N | 64 | 128 | 256 |
|---|----|-----|-----|
| with `?` | 1.0s | 8.1s | 69.2s |
| without `?` | 0.10s | 0.12s | 0.22s |

The struct, the field types, and the drop glue are identical; only the `?`
early-return cleanup paths differ. Each `?` that yields `Err` must drop the fields
already initialized, so the partially-initialized-struct cleanup is `O(N²)` basic
blocks, and the optimizer is super-linear over that CFG.

`-Copt-level` sensitivity (N=128, nightly): `O0` 0.4s · `O1` 3.4s · `O2` 8.7s ·
`O3` 8.3s · `Os`/`Oz` 6.0s. Present at every optimization level ≥ 1; `O0` is fine.

### Where the time goes

`-Zllvm-time-trace`, leaf-pass self-time, N=128, nightly (LLVM 22):

| pass | self-time | % of opt |
|---|---|---|
| `InstCombinePass` | 1.63s | 36% |
| `CorrelatedValuePropagationPass` | 0.97s | 21% |
| `JumpThreadingPass` | 0.67s | 15% |
| `GVNPass` | 0.34s | 8% |

Each grows super-linearly. N=64→128 (2× input): InstCombine 9.6×, CVP 10.8×,
JumpThreading 8.4×. (In the original real-world crate, `CorrelatedValuePropagationPass`
dominated even more strongly — ~99% of LLVM time — presumably because the per-field
body inlined more value-range-rich code.)

### Regression / version history

N=64, `-O -Ccodegen-units=1`:

| rustc | LLVM | N=64 wall |
|---|---|---|
| 1.90.0 | 20.1.8 | 11.9s |
| 1.91.0 | 21.1.2 | **20.7s** |
| 1.94.1 | 21.1.8 | 1.0s |
| 1.96.0 (stable) | 22.1.2 | 1.0s |
| 1.98.0-nightly (e7815e522) | 22.1.6 | 1.0s |

A large constant-factor improvement landed with LLVM 21.1.8 (rustc 1.94), but the
growth remains super-linear on current stable and nightly — it is just shifted to
larger N. Still clearly reproduces on `1.98.0-nightly (e7815e522 2026-06-04)`:
N=128 = 9.7s, control (no `?`) = 0.2s.

### Meta

`rustc --version --verbose`:

```
rustc 1.98.0-nightly (e7815e522 2026-06-04)
host: aarch64-apple-darwin
release: 1.98.0-nightly
LLVM version: 22.1.6
```

Also reproduces on stable `1.96.0 (ac68faa20 2026-05-25)` (LLVM 22.1.2).

<!--
@rustbot label +I-compiletime +A-LLVM +A-codegen +T-compiler +C-bug
Possibly related (different root cause — that one also reproduces on Cranelift, so
it is frontend/mono, not LLVM-opt): #122944
-->
