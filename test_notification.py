import sys
sys.path.append('.')
import dual_sleeper

print("=================================================================")
print(" Dual Sleeper - Windows デスクトップトースト通知 テスト実行")
print("=================================================================")
print()

config = dual_sleeper.load_config()

dual_sleeper.send_notifications(
    config,
    "📍 端末から【西 約 16.3 km】地点で雷を検知！\n離席スリープの動作を「休止状態」へ事前昇格しました。",
    is_weather_alert=True,
    weather_title="⚡️ 近隣雷接近アラート (WARNING)"
)

print()
print("[完了] 送信完了！画面右下のトースト通知または通知センターをご確認ください。")
print("(※Telegram / Discord を設定中の場合は、そちらにも同時送信されます)")
print()
