$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& "$projectRoot/.venv/Scripts/python.exe" -m streamlit run "$PSScriptRoot/app.py"
