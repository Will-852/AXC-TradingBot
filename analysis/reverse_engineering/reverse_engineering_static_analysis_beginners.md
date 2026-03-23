# Reverse Engineering for Beginners: How to Perform Static Analysis on any Piece of Software

- **Source**: https://www.youtube.com/watch?v=krmxFEOaHss
- **Duration**: ~55 min (long-form tutorial)
- **Channel**: CyberFlow / security education

---

## Summary

A comprehensive beginner tutorial on x86 reverse engineering, covering hardware fundamentals through to hands-on malware static analysis using Cutter disassembler.

---

## Key Topics

### 1. Historical Context
- Enigma machine in WWII -> Alan Turing's "Bombe" = hardware reverse engineering
- RE has existed for centuries; now critical for cybersecurity

### 2. x86 Architecture Fundamentals
- **Registers**: EAX (accumulator), ESI/EDI (source/dest index), EBP (base pointer), ESP (stack pointer), EIP (instruction pointer), EFLAGS
- 32-bit architecture (x86 name from Intel 8086 lineage)
- EBX, ECX, EDX are aliases/extensions of EAX family
- CPU components: Registers -> ALU (arithmetic/logic) -> Control Unit -> RAM

### 3. Number Systems
- Decimal / Binary / Hexadecimal conversion table
- Hex uses A-F after 9 (A=10, F=15)
- Bitwise operations: AND, OR, XOR, NAND, NOT
- Must know manual conversion for when decompiler fails

### 4. Endianness
- **Little Endian**: starts with lowest byte (x86 default, most common)
- **Big Endian**: starts with biggest byte (network traffic)
- Applied to bytes in memory, NOT bits in registers
- Key skill: reading hex values "backwards" in disassembly

### 5. Stack & Memory
- Stack = temporary, LIFO (Last In, First Out)
- Grows downward (high address -> low address)
- Heap = dynamic allocation, not LIFO
- Main function pushed first -> popped last (program lifecycle)

### 6. x86 Assembly Instructions
| Instruction | Purpose |
|---|---|
| MOV | Move data between registers |
| JMP / JZ | Jump (conditional/unconditional) |
| ADD / SUB | Integer arithmetic |
| AND / XOR / NOT | Bitwise operators |
| CALL | Call function |
| RET | Return from function |
| PUSH / POP | Stack operations |
| LEA | Load effective address |

### 7. Hands-On: Malware Analysis with Cutter
- **Tool**: Cutter (open-source disassembler/decompiler, alternative to IDA Pro / Ghidra / GDB)
- **Process**:
  1. Open binary -> check metadata (architecture, endianness, format)
  2. Start at **main** function (not entry point)
  3. Use **graph view** for control flow visualization
  4. Read assembly top-down, identify pushes before API calls
  5. Cross-reference Windows API docs (e.g., InternetOpenW, URLDownloadToFileW)
  6. Add comments as you decode each block
  7. Fill in blanks on second pass

- **Findings from demo malware**:
  - Sets user agent "Mozilla 5.0" via InternetOpenW (5 params: agent, access type, proxy, proxy bypass, flags)
  - Downloads file via URLDownloadToFileW to `C:\...\cr4_33101_data.exe`
  - Conditional branch: test if download succeeded -> continue or exit
  - Further calls: LPCommandLine, GetModuleFileName

### 8. Practical Applications
- Malware analysis & understanding infection chains
- Bug bounty (understanding application internals)
- VM detection bypass (delete VM-check assembly lines)
- License check bypass (educational context)
- OS vulnerability research

---

## Key Takeaways

1. **Always start at main()** - entry point != main; main is where the program logic lives
2. **Graph view is essential** - shows conditional branches visually
3. **Little Endian reading** - stack is LIFO, so read API params bottom-up
4. **Cross-reference API docs** - match push params against Microsoft API documentation
5. **Comment obsessively** - track what you've decoded, fill blanks on second pass
6. **Static + Dynamic together** - static gives structure, dynamic gives runtime values
7. **Pattern recognition > IQ** - repetition builds the RE reflex

---

## Tools Mentioned
- **Cutter** (recommended for beginners) - open-source, graph view, decompiler
- IDA Pro (industry standard, expensive)
- Ghidra (NSA, free)
- GDB (CLI debugger)
- Strings, file, objdump, readelf, strace, ltrace (Linux CLI tools)
