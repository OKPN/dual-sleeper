# 🌩️ 落雷・豪雨自動保護ガイド (Lightning Protection Guide)

Dual Sleeper は、PC設置場所の周辺で落雷や豪雨、あるいはCAPE雷エネルギーの上昇が検知された際、離席中のPCを全自動で「休止状態（S4: 電源OFF）」へ退避させ、過電圧や落雷被害からPCとデータを保護する機能を備えています。

---

## 📍 1. 位置情報（緯度・経度）の設定手順

1. [Google マップ](https://maps.google.com) を開きます。
2. 自宅やPC設置場所の指定箇所を **右クリック** します。
3. メニューの一番上に表示される **「緯度, 経度」**（例: `35.681234, 139.767123`）をクリックしてコピーします。
4. `config.json` を開き、`"lightning_protection"` 内の `"location"` に貼り付けます。
5. `"enabled": true` に変更して保存します。

```json
"lightning_protection": {
  "enabled": true,
  "location": "35.681234, 139.767123",
  "check_interval_seconds": 300,
  "auto_hibernate": "state2_only",
  "forecast_protection": {
    "enabled": true,
    "lookahead_hours": 3
  }
}
```

---

## ⚙️ 2. 主な設定オプション

* **`enabled` (bool):** 機能の有効/無効 (`true` / `false`)。
* **`check_interval_seconds` (数値):** Open-Meteo 気象APIへの確認周期（秒）。初期値: `300` (5分)。
* **`auto_hibernate` (文字列):**
  * `"state2_only"`: モニター消灯中（離席中）のみ自動休止状態へ移行（推奨）。
  * `"always"`: 画面点灯中であっても雷検知で問答無用自動休止。
  * `"off"`: 休止せず通知のみ。
* **`forecast_protection` (オブジェクト):**
  * 直近指定時間内（`lookahead_hours`: 3時間等）に雷予報（CAPEエネルギー上昇）がある場合、事前警戒モードへ移行します。

---

## 🌤️ 3. 気象情報の確認方法

Telegram Bot が有効な場合、チャットで **/weather** と送信すると、現在の天候実況・落雷警報状況および今後12時間の CAPE 雷予報レポートがリアルタイムで受信できます。
