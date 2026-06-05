# Super-linear `rustc -O` compile time building an aggregate literal with diverging initializers

Minimal, self-contained reproducer for an LLVM-optimizer super-linearity. Building an
aggregate (struct or array) **literal** of `N` elements, where each element
initializer can **diverge** (early-return) and the element type has **drop glue**,
makes the function-level optimization pipeline grow ~**cubically** in `N`.

The pattern is common in generated/macro code (first hit in a real codebase where a
derive macro emitted a struct `new()` with ~124 `?`-initialized fields; that crate
took ~120 s to compile).

**The minimal trigger and a clean workaround** — see [Ablations](#ablations--minimal-trigger)
below. Short version: binding the fallible values to `let`s and *then* constructing
the aggregate (instead of interleaving `?` inside the literal) is **~165× faster** at
N=256 and stays linear.

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

## Ablations — minimal trigger

`python3 ablations.py <variant> 128` then compile with `-O -Ccodegen-units=1`
(measured on `rustc 1.94.1`):

| variant | wall | takeaway |
|---|---|---|
| `baseline` — struct of `String` via `?` | 5.6s | reference |
| `array` — array literal `[..]` via `?` | 5.6s | **any aggregate literal**, not structs |
| `manual` — `match`/`return` instead of `?` | 5.5s | **not `?`-specific**; any divergence |
| `box` — field type `Box<u64>` | 2.2s | drop glue alone is enough |
| `nodrop` — non-`Copy` field, **no `Drop`** | 0.1s | **needs drop glue** (not just moves) |
| `copy` — field type `u64` | 0.1s | — |
| `locals` — `N` `let x = make()?;`, no aggregate | 0.2s | **needs the aggregate literal** |
| `bind_then_build` — bind fallibly, then build | 0.2s | **divergence must be *inside* the literal** |
| `inlinable` — producer is inlinable | 79.3s | inlining the producer amplifies it ~14× |

Minimal trigger: **aggregate literal × element type with drop glue × diverging
initializers interleaved with construction**. Each diverging initializer needs a
cleanup path dropping the elements built so far → `O(N²)` cleanup CFG → super-linear
optimizer time. (`bind_then_build` returns the same 128-field aggregate as `baseline`,
so it is not the size of the return value.)

### Workaround / fix

Bind fallible values first, then build the aggregate infallibly:

```rust
let f0 = make_one(s ^ 0)?;
let f1 = make_one(s ^ 1)?;
// ...
Ok(Big { f0, f1, /* ... */ })
```

| N | 64 | 128 | 256 |
|---|----|-----|-----|
| interleaved `?` in literal | 1.0s | 8.1s | 66.1s |
| bind-then-build | 0.2s | 0.2s | **0.4s** |

`-Copt-level` sensitivity (nightly, N=128, baseline): `O0` 0.4s · `O1` 3.4s ·
`O2` 8.7s · `O3` 8.3s · `Os`/`Oz` 6.0s. Present at every level ≥ 1; `O0` unaffected.

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

## Still slow on current stable + nightly

`-O -Ccodegen-units=1`:

| rustc | LLVM | N=64 | N=128 |
|---|---|---|---|
| 1.94.1 | 21.1.8 | 1.0s | 8.1s |
| 1.96.0 (stable) | 22.1.2 | 1.0s | 8.3s |
| 1.98.0-nightly (e7815e522) | 22.1.6 | 1.0s | 9.7s |

Older toolchains were far worse (LLVM 20, rustc 1.90: N=128 = **503s**); a large
constant-factor fix landed in LLVM 21.1.8, but the growth is **still super-linear** on
current stable/nightly — just shifted to larger N.

## MIR evidence (the cleanup explosion is in drop elaboration, not LLVM)

`drop`-terminator / basic-block / cleanup-block counts in the optimized MIR of
`build()` (`rustc -O --emit=mir`), via `python3 mir_count.py`:

| N | variant | bbs | drops | cleanup bbs |
|---|---|---|---|---|
| 16 | baseline | 321 | 254 | 269 |
| 16 | bind_then_build | 82 | 30 | 31 |
| 32 | baseline | 1153 | 1022 | 1053 |
| 32 | bind_then_build | 162 | 62 | 63 |
| 64 | baseline | 4353 | 4094 | 4157 |
| 64 | bind_then_build | 322 | 126 | 127 |

For the interleaved-divergence aggregate literal these are **≈ N²** (4094 ≈ 64² at
N=64); for bind-then-build they are **≈ 2N**. Each construction point gets a cleanup
chain dropping the suffix of already-initialized elements, so a partially-initialized
aggregate produces a quadratic cleanup ladder at the MIR level — already present before
the LLVM passes run.
