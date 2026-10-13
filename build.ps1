# 在本目录生成 dist\GrokWorkbench.exe。需要已安装 Python 3.11 或更新版本。
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
py -3 -m pip install --disable-pip-version-check pyinstaller
py -3 -m PyInstaller --noconfirm --clean --windowed --onefile `
  --name "GrokWorkbench" `
  --icon "grok.ico" `
  --add-data "static;static" `
  app.py
Write-Host "已生成 dist\GrokWorkbench.exe"
