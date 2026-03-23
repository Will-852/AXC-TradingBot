# Learn Low-Level Programming: ARM Assembly CTF Challenge

- **Channel**: Low Level Learning (security researcher)
- **Topic**: PicoCTF ARM assembly reverse engineering challenge

---

## Core Argument

> Even if you program in higher-level languages like JavaScript or Python, knowing computer architecture fundamentals makes you a better programmer. There's no other opportunity your computer has but to behave in simple computer architecture ways.

**Best way to learn**: Capture The Flag (CTF) challenges — one per week for a year = massive knowledge gain.

---

## CTF Walkthrough: PicoCTF ARM Assembly

### Setup
- Platform: PicoCTF (by CMU / Plaid Parliament of Pwning)
- Challenge: ARM Assembly Zero — given an .S file, determine program output with specific arguments
- Architecture: ARMv8-A (64-bit ARM assembly)
- Source was compiled from C -> assembly (not to machine code)

### Key ARM Assembly Concepts Taught

| Concept | Explanation |
|---|---|
| `.text` section | Code (readable + executable, NOT writable) |
| `.data` section | Data (readable + writable) |
| `x29` | Frame pointer (bottom of stack frame) |
| `x30` | Link register (return address after function call) |
| `x0` | First argument register + return value |
| `x1` | Second argument register |
| `w0` / `w19` | 32-bit width of registers (vs `x0` = 64-bit) |
| `stp` | Store pair — save two registers to stack |
| `sub sp` | Create room on stack for function's stack frame |
| `str` / `ldr` | Store / Load register to/from memory |
| `cmp` | Compare two registers, set CPU flags |
| `b.lt` | Branch if less than (conditional jump) |
| `bl` | Branch and Link = function call |

### Solving Process

1. **Start at main**, not entry point
2. **argv access**: `argv` lives at `x29+32`, each pointer is 8 bytes (64-bit)
   - `argv[1]` = base + 8
   - `argv[2]` = base + 16
3. **atoi** called on both args (ASCII string -> integer)
4. **func_one** called with both integer args
5. **Reverse func_one logic**:
   - Compare arg1 vs arg2
   - If arg1 < arg2 → return arg2
   - Otherwise → return arg1
   - **func_one = MAX function**
6. **Answer**: `hex(max(arg1, arg2))` in the flag format

### Verification
```bash
aarch64-gcc -o chow chow.S -static
./chow 1 2  # returns max
```

---

## Key Takeaways

1. **One CTF per week** for a year = massive low-level knowledge foundation
2. ARM registers: x0-x30 (64-bit), w0-w30 (32-bit lower half)
3. Function calling convention: args in x0, x1, x2... return value in x0
4. **Read assembly top-down**, trace data flow through registers
5. Recognizing common patterns (MAX, MIN, sort) is the real skill
