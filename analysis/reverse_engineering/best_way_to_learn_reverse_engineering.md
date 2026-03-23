# The Best Way To Learn Reverse Engineering

- **Source**: https://www.youtube.com/watch?v=EfAVDb-0iCk
- **Duration**: ~7 min (concise roadmap)
- **Channel**: CyberFlow / security education

---

## Summary

A fast, structured roadmap for learning reverse engineering — from mindset to tools to daily practice routine. Focuses on building repeatable skills rather than one-off tricks.

---

## Core Philosophy

> "Reverse engineering is not mystical detective work. It's just stubborn curiosity with better tooling."
> "Bottom-up archaeology" — figure out human intent behind machine noise.

---

## The Learning Path

### Phase 1: Foundation (Compile -> Disassemble -> Compare)
1. Write tiny C programs
2. Compile them
3. Open binary in hex editor, then disassembler
4. Watch "3 lines of C explode into dozens of assembly instructions"
5. Learn: MOV, ADD, CALL, PUSH, RET — what they do and WHY the compiler generates them
6. Repeat until you stop guessing and start **recognizing patterns**

### Phase 2: Tools (Use but don't worship)
| Tool | Purpose | Note |
|---|---|---|
| GDB (+PWNDBG/GEF/PEDA) | Low-level debugging | Raw but powerful |
| IDA / Binary Ninja | Decompilation + graph views | Nicer UI |
| Radare2 (r2 -d -aa) | Full analysis + debugging | Built for reversing |
| file, strings, objdump, readelf | Quick recon | Fast, indispensable |
| strace, ltrace | Syscall/library tracing | Confirm theory |

**Critical warning**: "Decompilers LIE. They're chefs trying to recreate the recipe from the finished dish. Treat decompiled output as pseudo-code that needs verification. Disassemblers are 1:1. Decompilers are educated guesses."

### Phase 3: The Repeatable Process (Every Single Time)
1. `file` — learn the format
2. `strings` — sniff for obvious constants
3. Run in **isolated VM/container** (disconnect network if malware)
4. Load in analysis tool
5. **Static first**: skim functions, find suspicious calls, trace control flow, identify interesting globals/arrays
6. **Switch to dynamic**: set breakpoints, inspect registers
7. Key debugger commands: `x/s`, `info registers` (GDB); `px`, `dr` (r2)

### Phase 4: Daily Practice Routine
- Write function -> compile -> disassemble -> ask "what did the compiler do and why?"
- CTFs as sandbox (not trophies)
- Read others' writeups -> reproduce -> do it WITHOUT the writeup
- Automate boring parts: scripts to extract strings, dump memory, generate test programs
- **The feedback loop** (write C you think produced assembly -> compile -> compare) = fastest teacher

---

## Key Takeaways

1. **Static tells you the plan, Dynamic tells you the state** — use both
2. **Don't skip dynamic analysis** — memory inspection, stepping, strace/ltrace confirm theory
3. **Breakpoints placement matters** — after loops, before exits, snapshot final state
4. **Follow registers when symbols missing** — compilers leave pointers in registers before print calls
5. **Always verify decompiler output** — step through assembly to confirm
6. **Isolated environment always** — VM/container, disconnect network for malware
7. **Pattern hunger > IQ** — first few times = chaos, then you start spotting compiler fingerprints

---

## Mindset
- Be patient AND greedy for patterns
- Less IQ, more pattern recognition
- Comment obsessively, rename functions, make IDE look like your brain
- "Propagate your hypothesis through the file"
