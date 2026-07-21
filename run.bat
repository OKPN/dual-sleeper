@echo off
chcp 65001 >nul
title Dual Sleeper

echo ==================================================
echo  Dual Sleeper - 起動スクリプト (仮想環境対応)
echo ==================================================

:: 1. Python の存在確認
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [エラー] Python がインストールされていないか、PATH に通っていません。
    echo Python 3.8 以上をインストールしてから再度お試しください。
    pause
    exit /b 1
)

:: 2. Python 仮想環境 (.venv) の自動作成
if not exist ".venv" (
    echo [1/3] Python 仮想環境 (.venv) を作成しています...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [エラー] 仮想環境の作成に失敗しました。
        pause
        exit /b 1
    )
    echo [1/3] 仮想環境を作成しました。
) else (
    echo [1/3] 既存の仮想環境 (.venv) を使用します。
)

:: 3. 仮想環境の有効化
call .venv\Scripts\activate.bat

:: 4. 依存ライブラリ (psutil) の自動インストール
echo [2/3] 依存ライブラリをチェック・インストール中 (psutil)...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [エラー] ライブラリのインストールに失敗しました。
    pause
    exit /b 1
)

:: 5. 設定ファイル config.json の自動作成 (存在しない場合)
if not exist "config.json" (
    if exist "config.json.example" (
        echo [3/3] config.json が見つからないため、config.json.example から自動生成します...
        copy config.json.example config.json >nul
    )
)

echo [3/3] 準備完了。Dual Sleeper を起動します...
echo ==================================================
echo.

python dual_sleeper.py

pause
