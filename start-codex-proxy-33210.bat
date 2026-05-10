@echo off
setlocal

set "HTTP_PROXY=http://127.0.0.1:33210"
set "HTTPS_PROXY=http://127.0.0.1:33210"
set "ALL_PROXY=http://127.0.0.1:33210"
set "NO_PROXY=localhost,127.0.0.1,::1"

set "CODEX_APP_ID=OpenAI.Codex_2p2nqsd0c76g0!App"
start "" explorer.exe "shell:AppsFolder\%CODEX_APP_ID%"
