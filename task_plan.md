# Task Plan — MM v4: Dual-Layer Hybrid + Cancel Defense

## 核心設計（BMD verified + bankroll-aware）

```
Dual-layer 設計，但 bankroll-adaptive：

Layer 1 — HEDGE（equal shares UP + DN）
  Combined < $1.00 → guaranteed profit if both fill
  Minimum budget: 5 shares × combined price ≈ $4.75
  → 需要 bankroll ≥ $48 (at 10% cap)

Layer 2 — DIRECTIONAL（naked shares on likely side）
  Extra shares @ fair - spread (NOT fixed $0.475)
  68% accuracy → +EV but has variance
  Minimum: 5 shares × bid price

Bankroll gates:
  < $48: directional only (hedge 放唔落 CLOB minimum)
  $48-99: Zone 1 hedge / Zone 2-3 hedge+directional
  $100+: full dual-layer
```

### Zone Design

```
Zone 0 (fair 0.43-0.50): SKIP — no directional edge
Zone 1 (fair 0.50-0.57): ONLY hedge (if bankroll allows)
  Budget: 100% → hedge (equal UP/DN)
  Fallback if bankroll too small: SKIP

Zone 2 (fair 0.57-0.65): hedge + directional (small)
  Budget: 50% hedge + 50% directional
  Fallback: directional only

Zone 3 (fair >0.65): hedge + directional (large)
  Budget: 25% hedge + 75% directional
  Fallback: directional only
```

### 數學驗證（$100 budget at 10% = $10, fair=0.60）

```
Zone 2: 50% hedge ($5) + 50% directional ($5)
  Hedge: 5.26 shares each @ $0.575/$0.375 = $5.00
    Both fill: 5.26 × $0.05 = +$0.26 guaranteed
  Directional: 8.70 shares UP @ $0.575 = $5.00
    Win (68%): 8.70 × $0.425 = +$3.70
    Lose (32%): 8.70 × $0.575 = -$5.00

  EV = 0.68 × ($0.26 + $3.70) + 0.32 × ($0.26 - $5.00)
     = 0.68 × $3.96 + 0.32 × (-$4.74)
     = $2.69 - $1.52 = +$1.18

At $40 bankroll (directional only, $4.00 budget):
  7.0 shares UP @ $0.575 = $4.00
  Win (68%): 7.0 × $0.425 = +$2.97
  Lose (32%): 7.0 × $0.575 = -$4.00
  EV = 0.68 × 2.97 - 0.32 × 4.00 = +$0.74
```

## Verified Data
- Brownian Bridge: 66-70% accuracy at T+1min（180d OOS verified）
- STRONG (>0.60): 70.0% WR, LEAN (0.55-0.60): 58.8% WR
- Paper v4: 2/2 wins (+$5.23) — early but positive
- Signal ceiling ~70-72%（OBI/CVD +1-3%）
- Maker rebate = 25% of taker fees → free edge

## BMD Findings (incorporated)
- 💀 $40 bankroll: hedge 放唔落 → pure directional（唔假裝有 dual-layer）
- 🔴 Fixed $0.475 directional bid → 用 fair-spread instead（better fill rate）
- 🔴 Hedge single-fill ≠ +EV from informed perspective（only both-fill guaranteed）
- 🟡 Indicator weight T=1min should be max 30%（唔係 70%）
- ✅ 12 RED/YELLOW bugs already fixed

## Phases

### Phase 1: Rewrite plan_opening() — dual-layer + bankroll-aware `status: pending`
- [ ] Bankroll gate: < $48 → directional only; $48+ → dual-layer
- [ ] 3-zone logic (hedge + directional)
- [ ] Directional bid = fair - spread (NOT fixed $0.475)
- [ ] Equal shares in hedge layer (guarantee preserved)
- [ ] 10% bankroll hard cap
- [ ] Unit test all zones + bankroll levels

### Phase 2: Cancel defense `status: pending`
- [ ] Store entry_price_snapshot per order
- [ ] Cancel directional if spot moves >0.05% since entry
- [ ] TTL: cancel unfilled after 60s
- [ ] Cancel all 2 min before window end
- [ ] Only cancel directional（hedge 留住 if bankroll allows）

### Phase 3: Fix indicator weight `status: pending`
- [ ] Max 30% indicator weight (唔係 70%)

### Phase 4: Paper 48h `status: pending`

### Phase 5: Live `status: blocked`

## Key Risk Matrix

| Risk | Impact | Mitigation |
|------|--------|------------|
| Fill rate < 50% | Low profit | Tighten spread |
| Adverse selection | Directional loses | Cancel defense |
| $40 bankroll too small for hedge | No dual-layer benefit | Grow bankroll to $48+ |
| Directional WR < 55% | EV negative | Cancel defense + reduce directional % |

## Decisions
| Decision | Rationale |
|----------|-----------|
| Bankroll-aware dual-layer | 唔假裝 $40 有 hedge — honest about minimum |
| Directional at fair-spread (唔係 fixed $0.475) | Better fill rate |
| Cancel defense (layered) | Primary adverse selection defense |
| Max 30% indicator weight | Bridge is near-deterministic at T=1min |
| Equal shares ONLY in hedge layer | Asymmetric ≠ guaranteed |
