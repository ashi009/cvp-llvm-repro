import sys
N=int(sys.argv[1]); o=["#![allow(dead_code)]","#[inline(never)]",
"pub fn make_one(x:u64)->String{ x.to_string() }","pub struct Big{"]
for i in range(N): o.append(f"  f{i}:String,")
o.append("}"); o.append("#[inline(never)]"); o.append("pub fn build(seed:u64)->Big{")
o.append("  Big{")
for i in range(N): o.append(f"    f{i}:make_one(seed ^ {i}),")
o.append("  }"); o.append("}")
print("\n".join(o))
