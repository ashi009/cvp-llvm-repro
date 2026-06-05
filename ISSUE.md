# Super-linear (~cubic) `-O` compile time building an N-element aggregate literal whose initializers can diverge

<!-- Suggested labels: I-compiletime, A-LLVM, A-codegen, A-mir-opt, T-compiler, C-bug -->

### Summary

Constructing an aggregate (struct or array) **literal** with `N` elements, where
each element initializer can **diverge** (early-return) and the element type has
**drop glue**, makes the optimized build scale roughly **cubically** in `N`. At
`N = 256` it takes **~66 s** at `-O`; an equivalent function that binds the fallible
values to `let`s first and *then* builds the aggregate compiles in **0.4 s**.

The cause appears to be the drop-flag / partial-initialization cleanup that MIR drop
elaboration emits for a partially-built aggregate: each diverging initializer needs a
cleanup path dropping the elements constructed so far, giving an `O(N²)` cleanup CFG
that the LLVM function pipeline (`InstCombine` / `CorrelatedValuePropagation` /
`JumpThreading`) then processes super-linearly.

I hit this in real macro-generated code: a derive macro emitting a `new()` with ~124
`?`-initialized fields took ~120 s to compile one crate.

Minimal reproducer, generator, ablations, and measurement scripts:
**<https://github.com/ashi009/cvp-llvm-repro>**

### Minimal reproducer

```rust
#[inline(never)]
pub fn make_one(x: u64) -> Result<String, ()> {
    if x == u64::MAX { Err(()) } else { Ok(x.to_string()) }
}

pub struct Big { f0: String, f1: String, /* ... */ f127: String }

// SLOW: divergence (`?`) interleaved inside the aggregate literal
#[inline(never)]
pub fn build(s: u64) -> Result<Big, ()> {
    Ok(Big {
        f0:   make_one(s ^ 0)?,
        f1:   make_one(s ^ 1)?,
        // ... N total ...
        f127: make_one(s ^ 127)?,
    })
}
```

```bash
python3 generate.py 128 > repro_128.rs
rustc -O -Ccodegen-units=1 --crate-type=lib --emit=obj -o /dev/null repro_128.rs
```

### Compile time vs N — `rustc 1.94.1`, `-O -Ccodegen-units=1`

| N | 16 | 32 | 64 | 96 | 128 | 192 | 256 |
|---|----|----|----|----|-----|-----|-----|
| wall | 0.1s | 0.2s | 1.0s | 3.2s | 8.1s | 23.8s | **69.2s** |

Doubling N (64→128→256) multiplies wall time ~8× each step → **≈ O(N³)**.

### What is and isn't required (ablations, N=128, `1.94.1`)

`python3 ablations.py <variant> 128` generates each:

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
| `inlinable` — producer is inlinable | **79.3s** | inlining the producer amplifies it ~14× |

`bind_then_build` returns the *same* 128-field aggregate as `baseline`, so it is not
the size of the returned value — only whether divergence is interleaved with the
aggregate construction.

So the minimal trigger is: **aggregate literal × element type with drop glue ×
diverging initializers interleaved with construction.**

### Workaround

Bind the fallible values first, then build the aggregate infallibly:

```rust
let f0 = make_one(s ^ 0)?;
let f1 = make_one(s ^ 1)?;
// ...
Ok(Big { f0, f1, /* ... */ })
```

At N=256 this is **0.4s vs 66s** (≈165×) and stays roughly linear in N.

### Where the time goes

`-Zllvm-time-trace`, leaf-pass self-time, N=128, nightly (LLVM 22):

| pass | self-time | % of opt |
|---|---|---|
| `InstCombinePass` | 1.63s | 36% |
| `CorrelatedValuePropagationPass` | 0.97s | 21% |
| `JumpThreadingPass` | 0.67s | 15% |
| `GVNPass` | 0.34s | 8% |

Each grows super-linearly: N=64→128 (2× input) → InstCombine 9.6×, CVP 10.8×,
JumpThreading 8.4×. `-Copt-level` (N=128): `O0` 0.4s · `O1` 3.4s · `O2` 8.7s ·
`O3` 8.3s · `Os`/`Oz` 6.0s — present at every level ≥ 1.

### Still slow on current stable + nightly

`-O -Ccodegen-units=1`:

| rustc | LLVM | N=64 | N=128 |
|---|---|---|---|
| 1.94.1 | 21.1.8 | 1.0s | 8.1s |
| 1.96.0 (stable) | 22.1.2 | 1.0s | 8.3s |
| 1.98.0-nightly (e7815e522) | 22.1.6 | 1.0s | 9.7s |

`bind_then_build` stays flat (N=128 = 0.2s) on all of the above. Older toolchains were
far worse (LLVM 20, rustc 1.90: N=128 = 503s); a large constant-factor fix landed in
LLVM 21.1.8, but the growth remains super-linear on current stable/nightly.

### Meta

```
rustc 1.98.0-nightly (e7815e522 2026-06-04)
host: aarch64-apple-darwin
LLVM version: 22.1.6
```

Also reproduces on stable `1.96.0 (ac68faa20 2026-05-25)` (LLVM 22.1.2).

<!--
@rustbot label +I-compiletime +A-LLVM +A-codegen +A-mir-opt +T-compiler +C-bug
Possibly related but different root cause (#122944 also reproduces on Cranelift, so
that one is frontend/mono, not LLVM-opt): rust-lang/rust#122944
-->
