#!/usr/bin/env python3
"""Headless Ghidra queries for Fighting Force (PyGhidra + ghidra_psx_ldr).

Setup (macOS example; on Windows point at your Ghidra and a JDK 21):
    export GHIDRA_INSTALL_DIR=~/Applications/ghidra_12.1.4_PUBLIC
    export JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home
    python3 tools/extract_disc_file.py disc/FightingForce.cue GAME.EXE disc/GAME.EXE

Commands (run with the PyGhidra venv's python):
    ff_ghidra.py import [EXE ...]          import + analyze (default disc/GAME.EXE)
    ff_ghidra.py decomp ADDR[,ADDR..]      decompile the functions containing ADDRs
                                           (creates a function at ADDR if none contains it)
    ff_ghidra.py decomp-all OUT.c          decompile every function into one file
    ff_ghidra.py disasm ADDR [N]           N instructions from ADDR
    ff_ghidra.py refs ADDR                 references TO ADDR (code and data)
    ff_ghidra.py callers ADDR              functions calling the function at ADDR
    ff_ghidra.py func ADDR                 function containing ADDR (entry, size)
    ff_ghidra.py strings PATTERN           defined strings matching PATTERN

The Ghidra project lives in ghidra/projects/ (gitignored: it holds game code).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "ghidra" / "projects"
PROJECT_NAME = "FightingForce"


def start():
    import pyghidra
    if not os.environ.get("GHIDRA_INSTALL_DIR"):
        raise SystemExit("set GHIDRA_INSTALL_DIR")
    pyghidra.start()
    return pyghidra


def open_flat(pyghidra, exe: str, analyze: bool = False):
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    path = ROOT / "disc" / exe
    return pyghidra.open_program(str(path), project_location=str(PROJECT_DIR),
                                 project_name=PROJECT_NAME, analyze=analyze,
                                 program_name=exe, nested_project_location=False)


def addr(flat, value: str):
    # toAddr(int) takes a signed Java int; the string form takes any address.
    return flat.toAddr(f"0x{int(value, 16):08X}")


def cmd_import(pyghidra, args):
    for exe in args.exe_list or ["GAME.EXE"]:
        with open_flat(pyghidra, exe, analyze=True) as flat:
            prog = flat.getCurrentProgram()
            fm = prog.getFunctionManager()
            print(f"{exe}: language={prog.getLanguageID()} "
                  f"image_base={prog.getImageBase()} functions={fm.getFunctionCount()}")


def cmd_decomp(pyghidra, args):
    from ghidra.app.decompiler import DecompInterface
    from ghidra.util.task import ConsoleTaskMonitor
    with open_flat(pyghidra, args.exe) as flat:
        prog = flat.getCurrentProgram()
        ifc = DecompInterface()
        ifc.openProgram(prog)
        for one in args.addr.split(","):  # several: ADDR1,ADDR2,...
            f = prog.getFunctionManager().getFunctionContaining(addr(flat, one))
            if f is None:
                # Code reached only through a function pointer (menu states,
                # jump tables) has no function yet: create one there.
                tx = prog.startTransaction("ff_ghidra: create function")
                try:
                    flat.disassemble(addr(flat, one))
                    f = flat.createFunction(addr(flat, one), None)
                finally:
                    prog.endTransaction(tx, True)
                if f is None:
                    print(f"// no function contains {one} (and none could be created)")
                    continue
                print(f"// created function at {one}")
            res = ifc.decompileFunction(f, 120, ConsoleTaskMonitor())
            print(f"// {f.getName()} @ {f.getEntryPoint()} size {f.getBody().getNumAddresses()}")
            print(res.getDecompiledFunction().getC() if res.decompileCompleted()
                  else f"// decompile failed: {res.getErrorMessage()}")


def cmd_decomp_all(pyghidra, args):
    """Decompile every function into one file (args.addr = output path)."""
    from ghidra.app.decompiler import DecompInterface
    from ghidra.util.task import ConsoleTaskMonitor
    out = Path(args.addr)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open_flat(pyghidra, args.exe) as flat, out.open("w") as fh:
        prog = flat.getCurrentProgram()
        ifc = DecompInterface()
        ifc.openProgram(prog)
        n = 0
        for f in prog.getFunctionManager().getFunctions(True):
            res = ifc.decompileFunction(f, 60, ConsoleTaskMonitor())
            fh.write(f"// ===== {f.getName()} @ {f.getEntryPoint()} size {f.getBody().getNumAddresses()}\n")
            fh.write(res.getDecompiledFunction().getC() if res.decompileCompleted()
                     else f"// decompile failed: {res.getErrorMessage()}\n")
            n += 1
        print(f"{n} functions -> {out}")


def cmd_disasm(pyghidra, args):
    with open_flat(pyghidra, args.exe) as flat:
        listing = flat.getCurrentProgram().getListing()
        a = addr(flat, args.addr)
        for _ in range(args.n):
            ins = listing.getInstructionAt(a)
            if ins is None:
                d = listing.getDataAt(a)
                print(f"{a}: <data> {d}")
                a = a.add(4)
                continue
            print(f"{ins.getAddress()}: {ins}")
            a = ins.getMaxAddress().add(1)


def cmd_refs(pyghidra, args):
    with open_flat(pyghidra, args.exe) as flat:
        prog = flat.getCurrentProgram()
        fm = prog.getFunctionManager()
        for r in prog.getReferenceManager().getReferencesTo(addr(flat, args.addr)):
            src = r.getFromAddress()
            f = fm.getFunctionContaining(src)
            print(f"{src} {r.getReferenceType()} in {f.getName() if f else '?'}")


def cmd_callers(pyghidra, args):
    with open_flat(pyghidra, args.exe) as flat:
        prog = flat.getCurrentProgram()
        f = prog.getFunctionManager().getFunctionContaining(addr(flat, args.addr))
        if f is None:
            raise SystemExit(f"no function contains {args.addr}")
        from ghidra.util.task import ConsoleTaskMonitor
        for c in f.getCallingFunctions(ConsoleTaskMonitor()):
            print(f"{c.getEntryPoint()} {c.getName()}")


def cmd_func(pyghidra, args):
    with open_flat(pyghidra, args.exe) as flat:
        f = flat.getCurrentProgram().getFunctionManager().getFunctionContaining(addr(flat, args.addr))
        print("none" if f is None else
              f"{f.getName()} entry={f.getEntryPoint()} body={f.getBody().getMinAddress()}.."
              f"{f.getBody().getMaxAddress()}")


def cmd_strings(pyghidra, args):
    rx = re.compile(args.pattern, re.I)
    with open_flat(pyghidra, args.exe) as flat:
        listing = flat.getCurrentProgram().getListing()
        for d in listing.getDefinedData(True):
            if d.hasStringValue():
                s = str(d.getValue())
                if rx.search(s):
                    print(f"{d.getAddress()}: {s!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["import", "decomp", "decomp-all", "disasm", "refs", "callers", "func", "strings"])
    ap.add_argument("addr", nargs="?")
    ap.add_argument("n", nargs="?", type=int, default=24)
    ap.add_argument("--exe", default="GAME.EXE")
    args, rest = ap.parse_known_args()
    args.exe_list = ([args.addr] if args.op == "import" and args.addr else []) + rest
    args.pattern = args.addr
    pyghidra = start()
    {"import": cmd_import, "decomp": cmd_decomp, "decomp-all": cmd_decomp_all, "disasm": cmd_disasm, "refs": cmd_refs,
     "callers": cmd_callers, "func": cmd_func, "strings": cmd_strings}[args.op](pyghidra, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
