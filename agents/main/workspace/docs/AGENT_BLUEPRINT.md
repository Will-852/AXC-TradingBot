# AGENT_BLUEPRINT
# 按需讀取 — 唔常駐
# 觸發: 建立新 agent / skill / 任務之前

## 系統生命體概念

OpenClaw係一個完整生命體，每個部分對應人體器官。
建立新嘢之前，必須問：
「呢個新嘢係邊個器官？佢喺生命體嘅位置係？」

器官對應：
- 新agent      → 器官（有SOUL.md = 有靈魂）
- 新script     → 肌肉（放scripts/）
- 新參數       → DNA（放config/params.py）
- 新模式       → 基因表現（放config/modes/）
- Agent間通訊  → 血液（放shared/）

唔可以：
- 肌肉(scripts)自己做決定 = 手不聽大腦指揮
- DNA(config)散落各處 = 基因突變
- 兩個器官寫同一個血液文件 = 血液污染

## 三本筆記簿

📕 靈魂 SOUL.md = 「我係邊個」永遠唔變
📒 暫存 MEMORY.md = 「而家發生咩事」常更新
📘 地圖 CLAUDE.md = 人類導航，零 token

原則: 三本唔重複，改一個唔影響其他兩個

## 兩條鏈

鏈一：決策（用 token）
用戶 → main → trader/scanner/heartbeat

鏈二：執行（零 token）
agent 決定 → Python 執行 → 結果寫 shared/

黃金法則: 能用 Python 做嘅，唔用 LLM

## 建立新 Agent 檢查清單
□ 有 SOUL.md（目的、邊界、原則）
□ 載入 system-memory skill
□ 明確：寫入邊個文件？讀取邊個文件？
□ 唔同其他 agent 搶寫同一文件
□ 係 Layer 1 定 Layer 2？
□ System prompt 預計大小？低於10K先用 tier2
□ openclaw.json 已更新？
□ 已備份？

## 建立新 Skill 檢查清單
□ 解決咩問題？
□ 有冇現有 skill 已做同樣嘢？
□ Status: EXPERIMENTAL
□ 150行以內
□ 唔重複其他 skill 內容
