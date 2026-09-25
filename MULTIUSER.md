# 多人試用版部署

本版使用現有 LINE Messaging API 的私訊 `userId` 辨識個人。朋友私訊 Bot「我的看板」後，收到十分鐘有效、使用一次即失效的登入連結。網頁提供獨立預算、區域、屋型、屋齡、面積、收藏與新上架／降價通知開關。評分為可解釋的規則，未使用語言模型；通勤時間沒有經過地圖驗證，不納入分數。

## 啟用前

1. 為現有 Render Web Service 建立持久 PostgreSQL，將 **內部連線字串** 設為 Render 環境變數 `DATABASE_URL`。目前 Render 免費 Web Service 的本機檔案會在重啟或部署後遺失，會員資料不能放在程式庫或暫存 SQLite。請勿把資料庫憑證提交 GitHub。資料庫會在第一次私訊「我的看板」時建表。
2. 在 Render 設定高熵固定 `FLASK_SECRET_KEY`（至少 32 個隨機 bytes 的十六進位字串）；它用於簽署網頁 session。換金鑰會登出所有使用者。
3. 在 GitHub Actions Repository **Variables** 設定 `MEMBER_NOTIFY_URL` = `https://kumamoto-home-track.onrender.com/members/notify`。原本的 `LINE_CHANNEL_ACCESS_TOKEN` Secret 留在 Actions，Render 亦需要同一 Messaging API token 及 `LINE_CHANNEL_SECRET`。
4. 確認 Render 部署新程式；用朋友帳號私訊 Bot「我的看板」，登入、儲存條件並勾選個人通知，再執行每日排程或手動執行 Actions。推播只通知新候選或降價，每人一次最多五件，其餘候選次日再送。

## 已知範圍

- 房源搜尋仍以原專案的總價約 7,299 萬円、15 年內與建物／土地門檻先篩選。因此朋友選擇更高預算、較舊屋齡或更小面積時，不會自動增加新的物件；拓寬爬取範圍是下一階段。
- 房源 SQLite 隨 GitHub Actions 更新並提交到公開專案；上面只有公開刊登的資料。私人會員資料只放 PostgreSQL。
- LINE 群組中的「我的看板」不發個人連結。推播前需用私訊加入及明確勾選通知。
- 每次執行先提交爬取的資料庫，再嘗試通知；通知服務逾時不會使資料庫更新消失，該步仍會在 Actions 顯示失敗。
