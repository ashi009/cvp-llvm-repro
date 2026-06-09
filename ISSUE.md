# Drop elaboration emits `O(N²)` cleanup for partially-initialized aggregate literals

An aggregate literal of `N` droppable elements whose initializers can diverge could emit `O(N²)` `drop` terminators, which chokes LLVM's CVP pass and costs substantial time at `-O`:

```
$ time rustc -O -Ccodegen-units=1 --crate-type=lib --emit=obj -o /tmp/r.o repro_128.rs
        9.20 secs
$ time rustc -O -Ccodegen-units=1 --crate-type=lib --emit=obj -o /tmp/r.o repro_64.rs
        1.14 secs
```

```rust
pub fn build(s: u64) -> Result<Big, ()> {
    Ok(Big { f0: make_one(s ^ 0)?, /* … N fields … */ f127: make_one(s ^ 127)? })
}
```

Repro: <https://github.com/ashi009/cvp-llvm-repro>. Binding the fallible values to `let`s first, then building the aggregate, keeps the MIR `O(N)`.

Tested on 1.96.0 stable (LLVM 22.1.2) and 1.98.0-nightly e7815e522 (LLVM 22.1.6).
