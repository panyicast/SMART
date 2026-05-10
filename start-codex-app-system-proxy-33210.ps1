$ErrorActionPreference = "Stop"

$proxyServer = "127.0.0.1:33210"
$appId = "OpenAI.Codex_2p2nqsd0c76g0!App"
$internetSettings = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"

Set-ItemProperty -Path $internetSettings -Name ProxyEnable -Type DWord -Value 1
Set-ItemProperty -Path $internetSettings -Name ProxyServer -Type String -Value $proxyServer
Set-ItemProperty -Path $internetSettings -Name ProxyOverride -Type String -Value "<local>;localhost;127.0.0.1;::1"

Start-Process explorer.exe "shell:AppsFolder\$appId"
