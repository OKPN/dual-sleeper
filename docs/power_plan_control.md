# ⚡ 電源プロファイル自動制御機能ガイド (Power Plan Control)

Dual Sleeper は、Windows OS 標準の電源プラン（GUID）を状況に応じてダイナミックに自動切替し、処理性能の最大化と放置時の極限節電を両立させます。

---

## ⚙️ 1. 設定方法 (`config.json`)

`config.json` の `"power_plan_control"` 内の `"enabled"` を `true` に変更することで有効化されます（初期値: `false` / 無効）。

```json
"power_plan_control": {
  "enabled": true,
  "restore_on_exit": true,
  "power_saver_on_idle_monitor_off": true,
  "ultimate_on_game": true,
  "high_performance_on_ai": true,
  "high_performance_on_cpu": true,
  "cpu_heavy_threshold_percent": 80,
  "cpu_heavy_duration_seconds": 5
}
```

---

## 🌟 2. 自動切替の動作モード

1. **完全放置消灯時 (State 2) の全自動節電 (`power_saver_on_idle_monitor_off`)**  
   操作から離脱してモニターが消灯した瞬間、自動的に OS 標準の **「省電力」プロファイル** へ切り替え、CPUのクロックと電圧を物理的に抑制して消費電力を大幅カットします。復帰時には安全に元の電源プランへ自動復元します。
2. **GPUゲーム中の高パフォーマンス昇格 (`ultimate_on_game`)**  
   ゲーム起動時（GPU使用率 30%以上）に自動的に **「究極のパフォーマンス（または高パフォーマンス）」** プランへ自動昇格し、フレームレートの低下を防ぎます。
3. **AI学習・推論時の昇格 (`high_performance_on_ai`)**  
   `python.exe` や `llama-server` 等が VRAM 4GB 以上を消費して AI 処理を行っている場合、自動的に **「高パフォーマンス」** プランへ昇格させ、PCIe 省電力モードによる生成遅延を防ぎます。
4. **CPU高負荷処理時の昇格 (`high_performance_on_cpu`)**  
   動画エンコード、大規模コンパイル、**Windows Update のインストール (`TiWorker.exe`等)** で CPU 80% 以上が 5 秒間継続した場合、自動的に **「高パフォーマンス」** プランへ昇格させます。

---

## 🛡️ 3. 安全設計

* **レジストリ非破壊設計:** Windows 10 / 11 の OS 標準プロファイル（GUID）のみを直接切り替えるため、カスタムプロファイルを乱立させず、OS の安定性を 100% 保ちます。
* **安全な元プラン復元:** アプリ終了時や操作復帰時には、アプリ起動前に設定されていた元の電源プランへ必ず安全復元します。
