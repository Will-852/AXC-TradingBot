# AGENT_BLUEPRINT
# 按需讀取 — 唔常駐
# 觸發: 建立新 agent / skill / 任務之前

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
