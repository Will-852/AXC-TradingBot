# Reverse Engineering LLM Training Data

- **Topic**: Three research approaches to infer/extract training data from language models
- **Relevance**: Understanding AI model vulnerabilities + data security

---

## Context

- No fully open-source near-SOTA LLM exists (training data never fully released)
- Only exception: Pythia series (EleutherAI, years ago)
- Training data is the second most expensive moat behind GPUs
- Anthropic reportedly bought millions of physical books, tore them up to scan for training data (won the court case)
- Synthetic data + chain-of-thought are also extremely valuable IP

---

## Three Attack Vectors

### 1. Statistical Inference (Black-box, API only)

**Paper**: "Can We Infer Confidential Properties of Training Data from LLMs?"

- **Method (PropInfer)**: Write prompts about a topic → generate hundreds of answers → label outputs → treat as noisy samples from training distribution → maximum likelihood estimator
- **What it leaks**: Meta-statistics only (e.g., HIV prevalence ratio, female patient ratio, programming language distribution)
- **Cannot do**: Recover any singular data point
- **Threat level**: Low — business-level stats, not individual records
- **Access needed**: API only (black-box)

**Math — Maximum Likelihood Estimator**:
```
Given N generated answers, k labeled as having property P:

  p̂ = k / N    (point estimate of true training ratio)

Confidence interval (normal approx):
  p̂ ± z · √(p̂(1-p̂) / N)

  where z = 1.96 for 95% CI

Example: 200 medical answers, 34 mention HIV
  p̂ = 34/200 = 0.17
  CI = 0.17 ± 1.96 × √(0.17 × 0.83 / 200) = 0.17 ± 0.052
  → estimated 12%-22% of training medical data mentions HIV
```

### 2. Repeated Token Exploit (Weights access preferred)

**Paper**: "Interpreting the Repeated Tokens Phenomenon in LLMs" (Google)

- **Method**: Feed 20-30 identical tokens → perplexity collapses → model emits memorized pre-training passages
- **Mechanism**: Attention sync heads always point to BOS token. When all input keys identical, softmax flattens → sync heads dominate

**Math — Why softmax flattens**:
```
Attention score: α_i = softmax(q · k_i / √d_k)

When all k_i are identical (repeated tokens):
  q · k_1 = q · k_2 = ... = q · k_n
  → softmax produces uniform distribution: α_i = 1/n for all i
  → BOS-focused heads (with learned bias toward position 0) dominate
  → output ≈ value vector stored at BOS position during pre-training
```
- **Example**: Pythia 12B + "as" repeated 50x → outputs rephrased paragraph from training website
- **Semi-controllable**: Add `def`/`class` for code, `# Abstract` for academic text, `copyright` for sensitive docs
- **Cannot do**: Deterministically extract specific string
- **Mitigation**: Train on synthetic repeated-token examples OR downscale sync heads → completely avoidable
- **Threat level**: Medium but easily patchable

### 3. Gradient-Based Data Selection (Most concerning)

**Paper**: "Approximating Language Model Training Data from Weights"

- **Method (SELECT)**: Requires BOTH base model + fine-tuned model weights
- **Applicable to**: DeepSeek, Qwen (both release base + fine-tuned versions)
- **Process**:
  1. Assemble large public corpus (Wikipedia, Common Crawl)
  2. For every sentence: forward + backward pass on base checkpoint → get gradient vector
  3. Compute full weight difference: fine-tuned minus base = real parameter shift
  4. Rank all sentence gradients by cosine similarity to real shift
  5. Take top-ranked sentences → apply to base model
  6. Repeat iteratively until no further improvement
- **Results**: ~90% of full data performance, up to 85% lexical overlap
- **Limitation**: Can only find data that exists in the public corpus
- **Threat level**: HIGH — produces usable training data, not just statistics

**Math — SELECT Algorithm**:
```
Definitions:
  θ_base  = base model weights
  θ_ft    = fine-tuned model weights
  Δw      = θ_ft - θ_base           (real weight shift from private training)
  D_pub   = {s_1, s_2, ..., s_N}    (public corpus, N sentences)

Step 1 — Compute per-sentence gradients:
  For each s_i ∈ D_pub:
    g_i = ∇_θ L(s_i; θ_base)       (gradient of loss w.r.t. base weights)
    This is the direction θ would move if trained on s_i for one step

Step 2 — Rank by alignment with real shift:
  score(s_i) = cos(g_i, Δw) = (g_i · Δw) / (‖g_i‖ · ‖Δw‖)

  Higher score = this sentence's training effect aligns
  with whatever the private data actually did

Step 3 — Greedy iterative selection:
  Selected = {}
  θ_current = θ_base
  Repeat:
    Pick s* = argmax_{s_i ∉ Selected} cos(g_i, θ_ft - θ_current)
    Selected = Selected ∪ {s*}
    θ_current = θ_current - η · g_{s*}    (simulate one training step)
    Stop when cos(θ_ft - θ_current, any g_i) < ε

Result: Selected ≈ proxy for private training data
  ~90% of fine-tuned model performance
  ~85% lexical overlap (vocabulary containment metric)
```

**Intuition**: 每句話嘅 gradient 係一個方向。搵啲方向加埋最接近真實 weight shift 嘅句子 = 逼近 private data 嘅效果。本質上係用公開語料做 greedy basis pursuit。

**Mitigations** (all have downsides):
- Mix in more private data sources (costly)
- Obfuscate weights (quantization etc.) — degrades model
- Refuse to open-source → bad for ecosystem

---

## Key Takeaways

1. **Black-box**: Can only leak distributional stats, not individual data
2. **Repeated tokens**: Memorized passages leak, but easily patched
3. **SELECT (gradient matching)**: Most threatening — recovers ~90% effective training data from weight diffs
4. Current SOTA models use complex synthetic datasets for agent tasks → harder to reverse engineer
5. Practical impact on top labs: limited for now, but concerning for fine-tuned model releases
