# Drop elaboration emits `O(N²)` cleanup for partially-initialized aggregate literals → super-linear `-O` compile time

<!-- Suggested labels: I-compiletime, A-mir-opt, A-codegen, A-LLVM, T-compiler, C-bug -->

### Summary

Building an aggregate (struct or array) **literal** of `N` elements, where each
element initializer can **diverge** (early-return) and the element type has **drop
glue**, makes compile time grow ~**cubically** in `N` at `-O` (`N=128` ≈ 8 s on
current stable/nightly, `N=256` ≈ 66 s).

The root cause is in **MIR drop elaboration**, not (only) LLVM: a partially-initialized
aggregate literal gets an `O(N²)` cleanup ladder — each construction point unwinds to a
chain that drops the *suffix* of already-initialized elements. The optimized MIR of the
function already contains ≈ N² `drop` terminators and cleanup blocks **before any LLVM
pass runs**. LLVM's function pipeline (`InstCombine` / `CorrelatedValuePropagation` /
`JumpThreading`) is then super-linear on that `O(N²)` CFG, compounding it to ~`O(N³)`
wall time.

Binding the fallible values to `let`s and *then* constructing the aggregate infallibly
keeps the MIR at `O(N)` and compile time linear — **~165× faster at N=256**.

First hit in real macro-generated code: a derive macro emitting a `new()` with ~124
`?`-initialized fields took ~120 s to compile one crate.

Repro, ablations, MIR/scaling scripts: **<https://github.com/ashi009/cvp-llvm-repro>**

### Root cause: `O(N²)` MIR cleanup

`drop`-terminator / basic-block / cleanup-block counts in the optimized MIR of
`build()` (`rustc -O --emit=mir`, via `python3 mir_count.py`):

| N | variant | basic blocks | `drop` terminators | cleanup blocks |
|---|---|---|---|---|
| 16 | aggregate literal (interleaved divergence) | 321 | 254 | 269 |
| 16 | bind-then-build | 82 | 30 | 31 |
| 32 | aggregate literal | 1153 | 1022 | 1053 |
| 32 | bind-then-build | 162 | 62 | 63 |
| 64 | aggregate literal | 4353 | **4094** | 4157 |
| 64 | bind-then-build | 322 | 126 | 127 |

For the aggregate literal these are **≈ N²** (4094 ≈ 64² at N=64); for bind-then-build
they are **≈ 2N**. The MIR cleanup for `build()` at N=4 already shows the ladder — each
field's construction point unwinds to a separate cleanup chain dropping a different-
length suffix of the initialized fields.

### Minimal reproducer

```rust
#[inline(never)]
pub fn make_one(x: u64) -> Result<String, ()> {
    if x == u64::MAX { Err(()) } else { Ok(x.to_string()) }
}

pub struct Big { f0: String, f1: String, /* ... */ f127: String }

#[inline(never)]
pub fn build(s: u64) -> Result<Big, ()> {
    Ok(Big {
        f0:   make_one(s ^ 0)?,
        f1:   make_one(s ^ 1)?,
        // ... N total; each initializer can diverge ...
        f127: make_one(s ^ 127)?,
    })
}
```

```bash
python3 generate.py 128 > repro_128.rs
rustc -O -Ccodegen-units=1 --crate-type=lib --emit=obj -o /dev/null repro_128.rs
```

### What is and isn't required (ablations, N=128, `1.94.1`)

`python3 ablations.py <variant> 128`:

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
initializers interleaved with construction**. (`bind_then_build` returns the *same*
128-field aggregate as `baseline`, so it is not the size of the return value.)

### Workaround

Bind the fallible values first, then build the aggregate infallibly:

```rust
let f0 = make_one(s ^ 0)?;
let f1 = make_one(s ^ 1)?;
// ...
Ok(Big { f0, f1, /* ... */ })
```

At N=256: **0.4 s vs 66 s** (≈165×), and MIR stays `O(N)`.

### Wall-clock scaling and the LLVM compounding

Compile time, `rustc 1.94.1`, `-O -Ccodegen-units=1`:

| N | 16 | 32 | 64 | 96 | 128 | 192 | 256 |
|---|----|----|----|----|-----|-----|-----|
| wall | 0.1s | 0.2s | 1.0s | 3.2s | 8.1s | 23.8s | **69.2s** |

`-Zllvm-time-trace` leaf-pass self-time (N=128, nightly): `InstCombinePass` 1.63s (36%),
`CorrelatedValuePropagationPass` 0.97s (21%), `JumpThreadingPass` 0.67s (15%),
`GVNPass` 0.34s (8%) — each itself super-linear over the `O(N²)` cleanup CFG
(N=64→128: InstCombine 9.6×, CVP 10.8×, JumpThreading 8.4×). `-Copt-level` (N=128):
`O0` 0.4s · `O1` 3.4s · `O2` 8.7s · `O3` 8.3s · `Os`/`Oz` 6.0s — any level ≥ 1.

### Still slow on current stable + nightly

| rustc | LLVM | N=64 | N=128 |
|---|---|---|---|
| 1.94.1 | 21.1.8 | 1.0s | 8.1s |
| 1.96.0 (stable) | 22.1.2 | 1.0s | 8.3s |
| 1.98.0-nightly (e7815e522) | 22.1.6 | 1.0s | 9.7s |

`bind_then_build` stays flat (N=128 = 0.2s) on all of the above. Older toolchains were
far worse (LLVM 20, rustc 1.90: N=128 = 503s); a large constant-factor fix landed in
LLVM 21.1.8, but the underlying `O(N²)` MIR cleanup is unchanged.

### Meta

```
rustc 1.98.0-nightly (e7815e522 2026-06-04)
host: aarch64-apple-darwin
LLVM version: 22.1.6
```

Also reproduces on stable `1.96.0 (ac68faa20 2026-05-25)` (LLVM 22.1.2).

@rustbot label +I-compiletime +A-mir-opt +A-codegen +A-LLVM +T-compiler +C-bug

Possibly related but a different root cause (#122944 also reproduces on Cranelift, so that one is frontend/mono): rust-lang/rust#122944
