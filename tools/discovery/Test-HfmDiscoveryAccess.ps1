<#
.SYNOPSIS
    Pre-flight check: does the current account have the permissions Invoke-HfmDiscovery.ps1 needs?

.DESCRIPTION
    READ-ONLY. Runs one small test per permission and prints PASS / PARTIAL / FAIL / INFO,
    the discovery section it affects, and what to request if it fails. Nothing is changed.

    Run it as your normal (non-elevated) account, in the same places you will run discovery:
      - on the jump server, with -NodesFile (and -LbUrl) to test remote access
      - on each EPM server you can log on to, without parameters

.EXAMPLE
    powershell -NoProfile -File .\Test-HfmDiscoveryAccess.ps1
    powershell -NoProfile -File .\Test-HfmDiscoveryAccess.ps1 -NodesFile .\nodes.csv -LbUrl https://<lb-address>
#>
[CmdletBinding()]
param(
    [string]   $NodesFile,
    [string]   $LbUrl,
    [string[]] $SearchDrives = @('C', 'D', 'E', 'F', 'G')
)

$results = New-Object System.Collections.Generic.List[object]
function Add-Result($Check, $Section, $Result, $Detail, $IfNotOk) {
    $results.Add([pscustomobject]@{ Check = $Check; Section = $Section; Result = $Result; Detail = $Detail; IfNotOk = $IfNotOk })
}
function Short($e) { ($e.Exception.Message -split "`r?`n")[0] }

# ---------------------------------------------------------------- can the script run at all?
$lang = $ExecutionContext.SessionState.LanguageMode
if ($lang -eq 'FullLanguage') {
    Add-Result 'PowerShell language mode' 'All' 'PASS' "FullLanguage, PowerShell $($PSVersionTable.PSVersion)" ''
} else {
    Add-Result 'PowerShell language mode' 'All' 'FAIL' "$lang (AppLocker/WDAC policy)" 'Ask the server team for an exception for the discovery script, or run its commands by hand'
}

$eff = Get-ExecutionPolicy
$gpo = Get-ExecutionPolicy -List | Where-Object { $_.Scope -in 'MachinePolicy', 'UserPolicy' -and $_.ExecutionPolicy -ne 'Undefined' }
if ($eff -in 'Unrestricted', 'RemoteSigned', 'Bypass') {
    Add-Result 'Execution policy' 'All' 'PASS' "Effective policy: $eff" 'If the file was downloaded, run Unblock-File on it first'
} elseif ($gpo) {
    Add-Result 'Execution policy' 'All' 'FAIL' "Set by Group Policy: $(($gpo | ForEach-Object { "$($_.Scope)=$($_.ExecutionPolicy)" }) -join ', ')" 'Group Policy cannot be overridden: ask for the script to be signed or for an exception'
} else {
    Add-Result 'Execution policy' 'All' 'PARTIAL' "Effective policy: $eff" 'Start it with: powershell -ExecutionPolicy Bypass -File .\Invoke-HfmDiscovery.ps1 (this session only, if your policy allows)'
}

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$elevated = (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$groups = (whoami /groups /fo csv | ConvertFrom-Csv).'Group Name'
$interesting = @($groups | Where-Object { $_ -match 'Administrators|Remote Management Users|Event Log Readers|Performance Monitor Users|Remote Desktop Users' })
Add-Result 'Account and groups' 'All' 'INFO' ("{0}; elevated={1}; groups: {2}" -f $id.Name, $elevated, ($interesting -join ', ')) 'Admin rights are NOT required; run discovery non-elevated'

try {
    $probe = Join-Path $env:USERPROFILE 'hfm_access_probe.tmp'
    'x' | Out-File $probe -ErrorAction Stop; Remove-Item $probe -ErrorAction Stop
    Add-Result 'Write output folder' 'All' 'PASS' $env:USERPROFILE ''
} catch { Add-Result 'Write output folder' 'All' 'FAIL' (Short $_) 'Run with -OutDir pointing to a folder you can write to' }

# ---------------------------------------------------------------- local reads
try { Get-CimInstance Win32_Service -Filter "Name='WinRM'" -ErrorAction Stop | Out-Null; Add-Result 'Query local services' '1' 'PASS' '' '' }
catch { Add-Result 'Query local services' '1' 'FAIL' (Short $_) 'Request local WMI read (root\cimv2) on this server' }

try { Get-NetTCPConnection -State Listen -ErrorAction Stop | Select-Object -First 1 | Out-Null; Add-Result 'List listening ports' '2' 'PASS' '' '' }
catch { Add-Result 'List listening ports' '2' 'FAIL' (Short $_) 'Run netstat -ano by hand, or request local WMI read' }

$up = foreach ($d in $SearchDrives) {
    $root = "$($d):\"
    if (Test-Path $root) { Get-ChildItem -Path $root -Directory -Recurse -Depth 4 -Filter 'user_projects' -ErrorAction SilentlyContinue | Select-Object -First 1 }
}
$up = @($up) | Select-Object -First 1
if (-not $up) {
    Add-Result 'Read EPM configuration' '3' 'INFO' 'No user_projects folder visible (not an EPM server, or the folder is hidden from this account)' 'On an EPM server: request read access to the Oracle Middleware folder'
} else {
    $cfg = Get-ChildItem (Join-Path $up.FullName 'domains') -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName 'config\config.xml' } | Where-Object { Test-Path $_ } | Select-Object -First 1
    try {
        if (-not $cfg) { throw 'config.xml not found under the domains folder (may be access denied on the folder)' }
        Get-Content $cfg -TotalCount 1 -ErrorAction Stop | Out-Null
        Add-Result 'Read EPM configuration' '3' 'PASS' $up.FullName ''
    } catch { Add-Result 'Read EPM configuration' '3' 'FAIL' ("{0}: {1}" -f $up.FullName, (Short $_)) 'Request read-only NTFS access to the Oracle Middleware folder (config, bin and diagnostics only)' }
}

try {
    $t = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskPath -notlike '\Microsoft\*' })
    if ($elevated) { Add-Result 'List scheduled tasks' '4' 'PASS' "$($t.Count) non-Microsoft tasks visible" '' }
    else { Add-Result 'List scheduled tasks' '4' 'PARTIAL' "$($t.Count) visible; non-admin accounts may not see tasks created by admins or SYSTEM" 'Ask the server team to confirm the full task list (Task Scheduler as admin)' }
} catch { Add-Result 'List scheduled tasks' '4' 'FAIL' (Short $_) 'Ask the server team for the scheduled task list' }

try { Get-WinEvent -LogName System -MaxEvents 1 -ErrorAction Stop | Out-Null; Add-Result 'Read System event log' '5' 'PASS' '' '' }
catch { Add-Result 'Read System event log' '5' 'FAIL' (Short $_) 'Request membership of the local "Event Log Readers" group' }

try { Get-Volume -ErrorAction Stop | Out-Null; Add-Result 'Read disk volumes' '6' 'PASS' '' '' }
catch { Add-Result 'Read disk volumes' '6' 'PARTIAL' 'Get-Volume denied; the script falls back to Get-PSDrive' 'Nothing needed' }

$sqlKey = 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\Instance Names\SQL'
if (Test-Path $sqlKey) {
    try { Get-ItemProperty $sqlKey -ErrorAction Stop | Out-Null; Add-Result 'Read SQL Server registry' '7' 'PASS' '' '' }
    catch { Add-Result 'Read SQL Server registry' '7' 'FAIL' (Short $_) 'Ask the DBA whether Always On / clustering is used (Q12)' }
} else { Add-Result 'Read SQL Server registry' '7' 'INFO' 'No SQL Server instance on this server' '' }

$nr = 'C:\Program Files\New Relic\newrelic-infra\newrelic-infra.yml'
if (Test-Path $nr) {
    try { Get-Content $nr -TotalCount 1 -ErrorAction Stop | Out-Null; Add-Result 'Read New Relic config' '8' 'PASS' '' '' }
    catch { Add-Result 'Read New Relic config' '8' 'FAIL' (Short $_) 'Ask the New Relic owner which hosts and integrations are configured (Q47)' }
} else { Add-Result 'Read New Relic config' '8' 'INFO' 'Agent not installed here' '' }

# ---------------------------------------------------------------- remote access from this host
if ($NodesFile) {
    $nodes = Import-Csv $NodesFile | Where-Object { $_.Placeholder -like '*NODE*' } | Sort-Object Placeholder -Unique
    foreach ($n in $nodes) {
        $tcp = Test-NetConnection -ComputerName $n.Value -Port 5985 -WarningAction SilentlyContinue -InformationLevel Quiet
        if (-not $tcp) { Add-Result "WinRM port 5985: $($n.Placeholder)" '9' 'FAIL' 'Port not reachable from this host' 'Firewall rule needed (same request as action A08)'; continue }
        try {
            Get-CimInstance Win32_Service -ComputerName $n.Value -Filter "Name='WinRM'" -OperationTimeoutSec 20 -ErrorAction Stop | Out-Null
            Add-Result "Remote service query: $($n.Placeholder)" '9' 'PASS' '' ''
        } catch {
            Add-Result "Remote service query: $($n.Placeholder)" '9' 'FAIL' (Short $_) 'Needs "Remote Management Users" + WMI Remote Enable on root\cimv2 on the target (same request as action A07)'
        }
    }
}

if ($LbUrl) {
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $r = Invoke-WebRequest -Uri ($LbUrl.TrimEnd('/') + '/workspace/index.jsp') -UseBasicParsing -TimeoutSec 30 -ErrorAction Stop
        Add-Result 'Reach Workspace via load balancer' '10' 'PASS' "HTTP $($r.StatusCode)" ''
    } catch { Add-Result 'Reach Workspace via load balancer' '10' 'FAIL' (Short $_) 'Check proxy settings or ask network team for HTTPS access from this host' }
}

# ---------------------------------------------------------------- report
$results | Format-Table Result, Check, Section, Detail -AutoSize -Wrap
$fails = @($results | Where-Object { $_.Result -in 'FAIL', 'PARTIAL' })
if ($fails.Count) {
    Write-Host "`nWhat to request:" -ForegroundColor Yellow
    $fails | ForEach-Object { Write-Host (" - [{0}] {1}: {2}" -f $_.Result, $_.Check, $_.IfNotOk) }
}
$out = Join-Path $env:USERPROFILE ("hfm_access_check_{0}.csv" -f (Get-Date -Format 'yyyyMMdd_HHmm'))
$results | Export-Csv $out -NoTypeInformation -Encoding UTF8
Write-Host "`nSaved: $out (contains this server's paths and account name; keep it on the server)"
