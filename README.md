# `O(N²)` drop-elaboration cleanup for aggregate literals with diverging initializers

Repro for rust-lang/rust#157463. An aggregate literal of `N` droppable elements whose initializers can diverge emits `O(N²)` `drop` terminators, which chokes LLVM's CVP pass and costs super-linear time at `-O`.

```
$ time rustc -O -Ccodegen-units=1 --crate-type=lib --emit=obj -o /tmp/r.o repro_128.rs
        9.20 secs
$ time rustc -O -Ccodegen-units=1 --crate-type=lib --emit=obj -o /tmp/r.o repro_64.rs
        1.14 secs
```

rustc 1.96.0, LLVM 22.1.2 — 2× the fields, ~8× the time.

`generate.py N` emits the repro; `measure.sh [toolchain]` sweeps `N`; `mir_count.py` shows the `O(N²)` `drop` terminators in the optimized MIR. Workaround: bind the fallible values to `let`s first, then build the aggregate infallibly — stays `O(N)` (`workaround_bind_then_build_128.rs`).
