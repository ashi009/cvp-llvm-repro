#!/usr/bin/env python3
"""Ablation generator: isolate which precondition triggers the super-linear
compile time. Usage: python3 ablations.py <variant> <N>

Variants (compile each with `rustc -O -Ccodegen-units=1 --emit=obj`):
  baseline        struct of String, fields initialized via `?`            (SLOW)
  array           array literal of String via `?`                         (SLOW)
  manual          struct of String, manual `match`/return instead of `?`  (SLOW)
  box             struct of Box<u64> via `?`                              (SLOW)
  nodrop          struct of a non-Copy type with NO Drop impl             (fast)
  copy            struct of u64 (Copy)                                     (fast)
  locals          N `let x = make()?;`, no aggregate literal              (fast)
  bind_then_build fallible lets, THEN build the aggregate infallibly       (fast) <- workaround
  inlinable       baseline but the producer is inlinable                  (SLOWER, ~14x)
"""
import sys

variant, N = sys.argv[1], int(sys.argv[2])
L = ["#![allow(dead_code)]"]

def producer(ty, ctor, inline_never=True, fallible=True):
    if inline_never:
        L.append("#[inline(never)]")
    if fallible:
        L.append(f"pub fn make_one(x:u64)->Result<{ty},()>{{ if x==u64::MAX {{Err(())}} else {{Ok({ctor})}} }}")
    else:
        L.append(f"pub fn make_one(x:u64)->{ty}{{ {ctor} }}")

def struct_decl(ty):
    L.append("pub struct Big{" + "".join(f"f{i}:{ty}," for i in range(N)) + "}")

def literal_via_q():  # divergence interleaved inside the aggregate literal
    L.append("#[inline(never)] pub fn build(s:u64)->Result<Big,()>{Ok(Big{"
             + "".join(f"f{i}:make_one(s^{i})?," for i in range(N)) + "})}")

if variant == "baseline":
    producer("String", "x.to_string()"); struct_decl("String"); literal_via_q()
elif variant == "array":
    producer("String", "x.to_string()")
    elems = ",".join(f"make_one(s^{i})?" for i in range(N))
    L.append(f"#[inline(never)] pub fn build(s:u64)->Result<[String;{N}],()>{{Ok([{elems}])}}")
elif variant == "manual":
    producer("String", "x.to_string()"); struct_decl("String")
    inits = "".join(f"f{i}:match make_one(s^{i}){{Ok(v)=>v,Err(e)=>return Err(e)}}," for i in range(N))
    L.append(f"#[inline(never)] pub fn build(s:u64)->Result<Big,()>{{Ok(Big{{{inits}}})}}")
elif variant == "box":
    producer("Box<u64>", "Box::new(x)"); struct_decl("Box<u64>"); literal_via_q()
elif variant == "nodrop":
    L.append("pub struct W(u64);")  # non-Copy, trivial drop (no Drop impl)
    producer("W", "W(x)"); struct_decl("W"); literal_via_q()
elif variant == "copy":
    producer("u64", "x"); struct_decl("u64"); literal_via_q()
elif variant == "locals":
    producer("String", "x.to_string()")
    body = "".join(f"let f{i}=make_one(s^{i})?;" for i in range(N))
    keep = "".join(f"std::hint::black_box(&f{i});" for i in range(N))
    L.append(f"#[inline(never)] pub fn build(s:u64)->Result<(),()>{{{body}{keep}Ok(())}}")
elif variant == "bind_then_build":
    producer("String", "x.to_string()"); struct_decl("String")
    body = "".join(f"let f{i}=make_one(s^{i})?;" for i in range(N))
    fields = ",".join(f"f{i}" for i in range(N))
    L.append(f"#[inline(never)] pub fn build(s:u64)->Result<Big,()>{{{body}Ok(Big{{{fields}}})}}")
elif variant == "inlinable":
    producer("String", "x.to_string()", inline_never=False); struct_decl("String"); literal_via_q()
else:
    sys.exit(f"unknown variant {variant}")

print("\n".join(L))
