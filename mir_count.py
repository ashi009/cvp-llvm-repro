import re, subprocess, sys
def counts(variant, N):
    subprocess.run([sys.executable,"ablations.py",variant,str(N)],stdout=open("/tmp/m.rs","w"))
    subprocess.run(["rustc","-O","--crate-type=lib","--emit=mir","-o","/tmp/m.mir","/tmp/m.rs"],
                   env={"RUSTC_BOOTSTRAP":"1","PATH":__import__("os").environ["PATH"]},
                   stderr=subprocess.DEVNULL)
    txt=open("/tmp/m.mir").read().splitlines()
    # isolate fn build: from 'fn build(' until a line that is exactly '}'
    body=[]; inb=False
    for ln in txt:
        if ln.startswith("fn build("): inb=True
        if inb:
            body.append(ln)
            if ln=="}": break
    body="\n".join(body)
    bbs=len(re.findall(r"^    bb\d+",body,re.M))
    drops=len(re.findall(r"\bdrop\(",body))
    cleanups=body.count("(cleanup)")
    return bbs,drops,cleanups
print(f"{'N':>4} | {'variant':<16} | {'bbs':>5} {'drops':>6} {'cleanup_bbs':>11}")
for N in [4,8,16,32,64]:
    for v in ["baseline","bind_then_build"]:
        b,d,c=counts(v,N)
        print(f"{N:>4} | {v:<16} | {b:>5} {d:>6} {c:>11}")
