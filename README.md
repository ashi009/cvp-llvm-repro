# Super-linear `rustc -O` compile time for a `?`-chain building a struct of droppable fields

Minimal, self-contained reproducer for an LLVM-optimizer super-linearity: a single
function that initializes `N` **droppable** struct fields, each through the `?`
operator, makes the function-level optimization pipeline grow ~**cubically** in `N`.

The pattern is common in generated/macro code (it was first hit in a real codebase
where a derive macro emitted a struct `new()` with ~124 `?`-initialized fields; that
crate took ~120 s to compile).

## Reproduce

```bash
python3 generate.py 128 > repro_128.rs
rustc -O -Ccodegen-units=1 --crate-type=lib --emit=obj -o /dev/null repro_128.rs
# or sweep:  ./measure.sh           (default toolchain)
#            ./measure.sh nightly
```

Pre-generated files `repro_64.rs` and `repro_128.rs` are committed.
`generate.py N` emits a file of the form:

```rust
#[inline(never)]
pub fn make_one(x: u64) -> Result<String, ()> {
    if x == u64::MAX { Err(()) } else { Ok(x.to_string()) }
}

pub struct Big { f0: String, f1: String, /* ... */ f127: String }

#[inline(never)]
pub fn build(seed: u64) -> Result<Big, ()> {
    Ok(Big {
        f0:  make_one(seed ^ 0)?,
        f1:  make_one(seed ^ 1)?,
        // ... N total ...
        f127: make_one(seed ^ 127)?,
    })
}
```

## Compile time vs N (`rustc 1.94.1`, `-O -Ccodegen-units=1`)

| N | 16 | 32 | 48 | 64 | 96 | 128 | 192 | 256 |
|---|----|----|----|----|----|-----|-----|-----|
| wall | 0.1s | 0.2s | 0.5s | 1.0s | 3.2s | 8.1s | 23.8s | **69.2s** |

Doubling N from 64→128→256 multiplies wall time by ~8× each step → **≈ O(N³)**.

## The `?` is the trigger (control)

Same `N` droppable fields, built **infallibly** (`control_128_no_question_mark.rs`,
`make_one` returns `String` not `Result`):

| N | 64 | 128 | 256 |
|---|----|-----|-----|
| with `?`     | 1.0s | 8.1s | 69.2s |
| without `?`  | 0.10s | 0.12s | 0.22s |

Identical struct, identical drops — only the `?` early-return cleanup paths differ.
Each `?` that returns `Err` must drop the fields initialized so far, producing
`O(N²)` cleanup blocks; the optimizer is super-linear over that CFG.

`-Copt-level` sensitivity (nightly, N=128): `O0` 0.4s · `O1` 3.4s · `O2` 8.7s ·
`O3` 8.3s · `Os`/`Oz` 6.0s. Present at every level ≥ 1; `O0` is unaffected.

## Where the time goes (`-Zllvm-time-trace`, leaf-pass self-time)

N=128, nightly (LLVM 22.1.2):

| pass | self-time | % |
|---|---|---|
| `InstCombinePass` | 1.63s | 36% |
| `CorrelatedValuePropagationPass` | 0.97s | 21% |
| `JumpThreadingPass` | 0.67s | 15% |
| `GVNPass` | 0.34s | 8% |

These three/four passes each grow super-linearly. N=64→128 (2× input):
InstCombine 9.6×, CVP 10.8×, JumpThreading 8.4×.

## Version history

Measured at N=64, `-O -Ccodegen-units=1`:

| rustc | LLVM | N=64 wall |
|---|---|---|
| 1.90.0 | 20.1.8 | 11.9s |
| 1.91.0 | 21.1.2 | **20.7s** (worst) |
| 1.94.1 | 21.1.8 | 1.0s |
| 1.96.0 (stable) | 22.1.2 | 1.0s |
| 1.98.0-nightly (e7815e522) | 22.1.6 | 1.0s |

A large constant-factor improvement landed with LLVM 21.1.8 (rustc 1.94), but the
growth is **still super-linear** on current stable/nightly — it is merely shifted to
larger N.
