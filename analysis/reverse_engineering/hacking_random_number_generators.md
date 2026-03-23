# Hacking Random Number Generators (PRNG Reverse Engineering)

- **Topic**: Reversing pseudo-random number generators to predict outputs
- **Relevance**: Security fundamentals, cryptographic vs non-cryptographic randomness

---

## Core Insight

> Computers generate "random" numbers from totally predictable sequences, often billions of numbers long, produced by short mathematical formulas.

---

## PRNG Types & How to Reverse Them

### 1. Linear Congruential Generator (LCG)

**Forward formula**:
```
x_{n+1} = (a · x_n + c) mod m
```
Video example: `a = 9301, c = 49297, m = 233280`

**Hull-Dobell Theorem** — LCG has full period (m) iff:
1. `gcd(c, m) = 1` (c and m are coprime)
2. `a - 1` is divisible by all prime factors of m
3. If m is divisible by 4, then `a - 1` is also divisible by 4

**Reverse formula**:
```
x_n = a⁻¹ · (x_{n+1} - c) mod m
```
where `a⁻¹` is the **modular multiplicative inverse** of a mod m.

**Computing modular inverse** — find `a⁻¹` such that `a · a⁻¹ ≡ 1 (mod m)`:
- Exists iff `gcd(a, m) = 1`
- Method 1: Extended Euclidean Algorithm
- Method 2: `a⁻¹ = a^(φ(m)-1) mod m` (Euler's theorem, φ = Euler's totient)
- Method 3 (if m is prime): `a⁻¹ = a^(m-2) mod m` (Fermat's little theorem)

**Video example**: `a = 9301, a⁻¹ = 179509 (mod 233280)`
- Verify: `9301 × 179509 = 1,669,753,409` → `1,669,753,409 mod 233280 = 1` ✓

```python
# Python implementation
def lcg_forward(x, a=9301, c=49297, m=233280):
    return (a * x + c) % m

def lcg_reverse(x, a=9301, c=49297, m=233280):
    a_inv = pow(a, -1, m)  # Python 3.8+ modular inverse
    return (a_inv * (x - c)) % m

# Attack: given output x_{n+1}, recover input x_n
# Chain: lcg_reverse(lcg_reverse(x)) gives x_{n-2}, etc.
```

**Attack scenario**: Service uses LCG for 6-digit recovery codes
1. Request code on your account → observe output `x_{n+1}`
2. Compute `x_n = lcg_reverse(x_{n+1})` → this was the victim's code
3. Works because LCG state is shared across all users

**Real-world**: PHP `lcg_value()` uses similar generator — **unsafe for security codes**

### 2. XOR-shift Generators

**XOR truth table** (fundamental to all XOR-shift attacks):
```
A | B | A ⊕ B
0 | 0 |   0
0 | 1 |   1
1 | 0 |   1
1 | 1 |   0
```

**Self-inverse property**: `(A ⊕ B) ⊕ B = A` — XOR 同一個值兩次 = 還原

**Basic XOR-shift operation**:
```
y = x ⊕ (x << n)     # left shift variant
y = x ⊕ (x >> n)     # right shift variant
```

**Reversing XOR-shift (right shift example)**:
```
Given:  y = x ⊕ (x >> n)
```
Key insight: 右移 n bits 後，最高 n bits 無被改過（shift 入嚟嘅係 0）
```
Step 1: top n bits of y = top n bits of x (unaffected)
Step 2: use known top bits to recover next n bits:
        x[n..2n] = y[n..2n] ⊕ x[0..n]  (already known from step 1)
Step 3: repeat until all bits recovered
```

```python
# Reverse right-shift XOR
def unshift_right(y, shift, bits=64):
    x = y
    for i in range(shift, bits, shift):
        x ^= (x >> shift)
    return x & ((1 << bits) - 1)

# Reverse left-shift XOR (with mask)
def unshift_left(y, shift, mask, bits=64):
    x = y
    for i in range(shift, bits, shift):
        x ^= (x << shift) & mask
    return x & ((1 << bits) - 1)
```

**Xorshift128+ (JavaScript V8)**:
```
s1 = state0
s0 = state1
state0 = s0
s1 ^= (s1 << 23)                    # shift-XOR
s1 ^= (s1 >> 17)                    # shift-XOR
s1 ^= s0                            # XOR
s1 ^= (s0 >> 26)                    # shift-XOR
state1 = s1
output = (state0 + state1) as uint64
```
Each step individually reversible → chain = fully reversible.
Tool: **Z3 SMT solver** can brute-force state from ~3 consecutive outputs.

| Implementation | Used In | Difficulty |
|---|---|---|
| Xorshift128+ | JavaScript | Reversible (Z3 framework or manual inverse) |
| Xoroshiro128++ | Minecraft | Significantly harder (rotation + addition) |
| LFSR + XOR-shift + subtractshift + polynomial | Flash Player | Complex but every step proven reversible |

### 3. Exploits in Practice

**Luck Override (Clover game)**: Find specific seed → force desired outcome at known position

**Minesweeper (Seed Recovery Attack)**:
```
Given: observed mine positions in beginner board (9×9, 10 mines)
Search space: all possible seeds (e.g., 2^32 = 4,294,967,296)

For each candidate seed s:
  1. Init PRNG(s)
  2. Generate 10 mine positions using same algorithm as game
  3. Compare with observed positions
  4. If match → s is the seed

Once seed found:
  - Re-init PRNG(s), skip beginner draws
  - Predict all 99 mine positions in expert board (30×16)
```
Brute-force 2^32 seeds ≈ minutes on modern hardware (parallelizable)

---

## Security Recommendations

| Context | Recommendation |
|---|---|
| Offline/game numbers | Don't worry too much — almost all hackable |
| Login systems / tokens | **Must use cryptographically secure** generators |
| General | Don't leak PRNG state between programs |
| Skepticism | "Insanely lucky" events (5 jackpots in a row) should raise red flags |

---

## Key Takeaways

1. **All non-crypto PRNGs are reversible** — LCG, XOR-shift, LFSR, all of them
2. **Modular multiplicative inverse** = key tool for reversing LCG
3. **XOR reversibility** = fundamental property exploited in XOR-shift attacks
4. **Seed recovery** from partial output = practical attack (Minesweeper demo)
5. **Crypto-secure generators** are the ONLY safe choice for sensitive operations
6. Pattern in randomness = always question "luck"
