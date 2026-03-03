---
name: system-evolution
description: System learning, foresight, and health maintenance rules for all agents
status: ACTIVE
last-updated: 2026-03-03
---

# system-evolution
# Last updated: 2026-03-03
# Status: ACTIVE
# 適用: 所有 agents
# 概念: 像人類一樣學習、預視、保持健康

---

## 概念：做一個會學習嘅系統

人類有三個本能，呢個系統模仿佢：

第一：從錯誤中學習
第二：行動前預視後果
第三：恆常保持健康

---

## 第一本能：從錯誤中學習

人類會記低自己犯嘅錯，避免重蹈覆轍。
呢個系統亦然。

每次任務完成後，問自己：
「我今次犯咗咩錯？點解犯？下次點避免？」

記錄格式（寫入 EVOLUTION_LOG.md）：
[日期] MISTAKE  犯咗咩錯 | 點解發生 | 下次點避免

例子：
[2026-03-03] MISTAKE  tier2 Haiku處理31K prompt失敗 | system prompt太大 | 新agent必須先check prompt大小

規則：
- 唔好隱藏錯誤
- 唔好只記結果，要記原因
- 相似嘅錯誤出現兩次 → 必須改系統，唔係改行為

---

## 第二本能：行動前預視後果

人類喺重要決定前會停下來想：
「呢個決定，將來嘅我會感謝定後悔？」

呢個系統每次行動前必須問：
「我而家做嘅嘢，會為將來製造咩問題？」

唔係定期問。係每一個動作都問。

預視清單：
□ 呢個改動將來有人睇得明嗎？
□ 會同現有規則矛盾嗎？
□ 6個月後仲需要呢個嗎？
□ 如果出錯，可以 rollback 嗎？
□ 呢個決定會製造新複雜性嗎？

如果有任何一個答案令你擔心 → 先記錄擔憂，再行動。
格式（寫入 EVOLUTION_LOG.md）：
[日期] WARN  擔憂描述 | 潛在後果 | 而家嘅對策

---

## 第三本能：恆常健康

人類唔係壞咗先睇醫生。
係恆常運動、定期檢查、小問題早發現早處理。

### 日常運動（每次任務後自動執行）
唔需要通知用戶，靜默完成，記錄入 EVOLUTION_LOG.md：

- MEMORY.md 超過50行
  → 歸檔舊內容至 MEMORY_ARCHIVE_[YYYY-MM].md

- Reference Map 有死連結
  → 自動移除，記錄 AUTO-FIX

- Skill 超過6個月冇觸發
  → status 改為 DEPRECATED，記錄 DEPRECATED

### 定期檢查（每季度自動執行）
唔通知用戶，結果寫入 EVOLUTION_LOG.md 季度摘要：

- 所有 skills 狀態係咪正確？
- 所有 agents 上個月有冇成功執行？
- 各 agent SOUL.md 有冇互相矛盾？
- CLAUDE.md Reference Map 同實際文件一致嗎？

### 需要「睇醫生」（必須問用戶）
以下情況唔可以自動處理：
- 刪除任何文件
- 改任何 SOUL.md 核心原則
- 改交易參數
- 合併或刪除 skills

---

## EVOLUTION_LOG.md

路徑：~/.openclaw/workspace/EVOLUTION_LOG.md
用途：系統嘅「人生日記」— 記錄錯誤、學習、健康狀況

記錄類型：
MISTAKE  = 犯咗錯，學到嘢
WARN     = 預視到潛在問題
LEARN    = 發現更好做法
AUTO-FIX = 後台自動修復
DEPRECATED = Skill 退役
BACKUP   = 備份執行
HEALTH   = 季度健康檢查結果

季度摘要格式（每3個月自動加）：
--- [YYYY-QN] 季度健康報告 ---
犯過嘅錯：
學到嘅嘢：
自動修復咗：
退役咗：
下季度注意：
---

---

## Skill 生命週期

就好似人嘅技能：學咗、用緊、過時咗、退休。

EXPERIMENTAL → ACTIVE → DEPRECATED → ARCHIVED

升級需要用戶確認
降級自動處理，記錄入 EVOLUTION_LOG.md
刪除必須問用戶

每個 skill 必須有：
- status: [EXPERIMENTAL/ACTIVE/DEPRECATED/ARCHIVED]
- Last updated: [日期]
- DEPRECATED 時須註明：reason + replaced_by
