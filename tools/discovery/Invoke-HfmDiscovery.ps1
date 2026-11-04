<#
.SYNOPSIS
    READ-ONLY discovery of an Oracle EPM / HFM 11.2 environment on Windows Server.
    Collects the facts needed to answer the open dashboard questions (Q-IDs in brackets).

.DESCRIPTION
    Nothing is started, stopped, changed or installed. The script only reads services,
    listening ports, configuration files, scheduled tasks, event logs and volumes, and
    optionally tests network reachability from the jump server.

    Run it in two places:
      1. On the jump server with -NodesFile (and -LbUrl): tests what a monitoring host
         will be able to reach (ports, WinRM, remote service queries, the load balancer URL).
      2. On each EPM server you can log on to (web, HFM, DB node), without parameters:
         reads services, ports, WebLogic / OHS / Node Manager configuration, start-up
         scripts, scheduled tasks and service crash history.

    Output stays in -OutDir on the machine where it runs. Raw files contain real server
    names. A "masked" copy is always written with names, accounts and IPs replaced by
    placeholders, and combined into ONE text file (masked\00_ALL_IN_ONE.txt) that is also
    copied to the clipboard, so it can leave the server as a single paste.

.PARAMETER NodesFile
    Local CSV with columns Placeholder,Value - one row per hostname, FQDN or IP, e.g.
        Placeholder,Value
        {{WEB_NODE_01}},realhost02
        {{WEB_NODE_01}},realhost02.corp.local
    Keep this file on the jump server only. Never share or commit it.

.PARAMETER LbUrl
    Load balancer base URL users open, e.g. https://hfm.corp.local  (optional)

.EXAMPLE
    .\Invoke-HfmDiscovery.ps1                                   # on an EPM server
    .\Invoke-HfmDiscovery.ps1 -NodesFile .\nodes.csv -LbUrl https://hfm.corp.local   # on the jump server
#>
[CmdletBinding()]
param(
    [string]   $OutDir       = (Join-Path $env:USERPROFILE ("hfm_discovery_{0}_{1}" -f $env:COMPUTERNAME, (Get-Date -Format 'yyyyMMdd_HHmm'))),
    [string]   $NodesFile,
    [string]   $LbUrl,
    [string[]] $SearchDrives = @('C', 'D', 'E', 'F', 'G'),
    [int[]]    $ExtraPorts   = @(),
    [int]      $EventDays    = 30,
    [switch]   $NoClipboard     # skip copying the combined masked text to the clipboard
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$SummaryPath = Join-Path $OutDir '00_summary.txt'
"HFM discovery on $env:COMPUTERNAME at $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-File $SummaryPath -Encoding UTF8

$ServicePattern = 'Oracle|Hyperion|EPM|WebLogic|Financial|OHS|Node ?Manager|MSSQL|SQLAgent|SQL Server|New ?Relic|ClusSvc|Cluster Service'
$script:EpmServices = @()

# ---------------------------------------------------------------- helpers
function Note([string] $Text) { Add-Content -Path $SummaryPath -Value $Text -Encoding UTF8; Write-Host $Text }

function Save-Table([string] $Name, $Rows) {
    $path = Join-Path $OutDir "$Name.csv"
    if ($null -eq $Rows -or @($Rows).Count -eq 0) { 'No data' | Out-File ($path -replace '\.csv$', '.txt') -Encoding UTF8; return }
    @($Rows) | Export-Csv -Path $path -NoTypeInformation -Encoding UTF8
}

function Save-Text([string] $Name, $Lines) {
    $Lines | Out-File (Join-Path $OutDir "$Name.txt") -Encoding UTF8 -Width 400
}

function Invoke-Section([string] $Title, [scriptblock] $Body) {
    Write-Host "== $Title" -ForegroundColor Cyan
    try { & $Body } catch { Note "[$Title] skipped: $($_.Exception.Message)" }
}

function Get-ChildText($Node, [string] $Name) {
    $n = $Node.SelectSingleNode("*[local-name()='$Name']")
    if ($n) { return $n.InnerText } else { return '' }
}

# ---------------------------------------------------------------- 1. services   [Q07 Q08 Q15 Q21]
Invoke-Section 'Windows services (EPM, SQL, New Relic, cluster)' {
    $script:EpmServices = Get-CimInstance Win32_Service |
        Where-Object { $_.DisplayName -match $ServicePattern -or $_.Name -match $ServicePattern } |
        Select-Object Name, DisplayName, State, StartMode, StartName, PathName
    Save-Table '01_services' $script:EpmServices
    Note ("Services found: {0}" -f @($script:EpmServices).Count)
    foreach ($s in $script:EpmServices) { Note ("  {0,-45} {1,-9} {2,-9} {3}" -f $s.Name, $s.State, $s.StartMode, $s.DisplayName) }
}

# ---------------------------------------------------------------- 2. listening ports   [Q17]
Invoke-Section 'Listening TCP ports with owning process' {
    $procs = @{}
    Get-Process | ForEach-Object { $procs[$_.Id] = $_.ProcessName }
    $ports = Get-NetTCPConnection -State Listen |
        Select-Object LocalAddress, LocalPort, OwningProcess,
            @{ n = 'Process'; e = { $procs[[int]$_.OwningProcess] } } |
        Sort-Object LocalPort -Unique
    Save-Table '02_listening_ports' $ports
}

# ---------------------------------------------------------------- 3. EPM installation   [Q04 Q05 Q06 Q08 Q13 Q17 Q22 Q24 Q35]
# Bounded folder walk that skips (and notes) folders whose paths are too long or unreadable,
# instead of aborting the whole section.
function Find-Files([string] $Root, [string[]] $Names, [int] $MaxDepth) {
    $found = New-Object System.Collections.Generic.List[string]
    $queue = New-Object System.Collections.Generic.Queue[object]
    $queue.Enqueue(@($Root, 0))
    $skipped = 0
    while ($queue.Count -gt 0) {
        $item = $queue.Dequeue(); $dir = $item[0]; $depth = $item[1]
        try {
            foreach ($f in [IO.Directory]::EnumerateFiles($dir)) {
                if ($Names -contains [IO.Path]::GetFileName($f)) { $found.Add($f) }
            }
            if ($depth -lt $MaxDepth) {
                foreach ($sub in [IO.Directory]::EnumerateDirectories($dir)) { $queue.Enqueue(@($sub, $depth + 1)) }
            }
        } catch { $skipped++ }
    }
    if ($skipped) { Note "  ($skipped folders under $Root skipped: path too long or access denied)" }
    return $found
}

function Invoke-Part([string] $Title, [scriptblock] $Body) {
    try { & $Body } catch { Note "  [$Title] skipped: $($_.Exception.Message)" }
}

Invoke-Section 'EPM / WebLogic / OHS configuration' {
    $userProjects = foreach ($d in $SearchDrives) {
        $root = "$($d):\"
        if (Test-Path $root) {
            Get-ChildItem -Path $root -Directory -Recurse -Depth 4 -Filter 'user_projects' -ErrorAction SilentlyContinue
        }
    }
    if (-not $userProjects) { Note 'No EPM user_projects folder found on this server.'; return }

    foreach ($up in $userProjects) {
        Note "EPM user_projects found (path recorded in the raw output only)"

        # WebLogic domains: servers, clusters, machines, JDBC, Node Manager
        foreach ($dom in (Get-ChildItem (Join-Path $up.FullName 'domains') -Directory -ErrorAction SilentlyContinue)) {
            Invoke-Part "config.xml ($($dom.Name))" {
                $cfg = Join-Path $dom.FullName 'config\config.xml'
                if (-not (Test-Path $cfg)) { return }
                [xml] $x = Get-Content $cfg -Raw
                $adminNode = $x.SelectSingleNode("//*[local-name()='admin-server-name']")
                $adminName = if ($adminNode) { $adminNode.InnerText } else { '' }
                Note "WebLogic domain '$($dom.Name)', Admin Server name: $adminName"
                # Lists are built by assigning the loop output: '+=' fails inside nested script blocks in PowerShell
                $wlsServers = foreach ($s in $x.SelectNodes("/*[local-name()='domain']/*[local-name()='server']")) {
                    $name  = Get-ChildText $s 'name'
                    $ssl   = $s.SelectSingleNode("*[local-name()='ssl']/*[local-name()='listen-port']")
                    $stuck = $s.SelectSingleNode(".//*[local-name()='stuck-thread-max-time']")
                    [pscustomobject]@{
                        Domain = $dom.Name; Server = $name; IsAdminServer = ($name -eq $adminName)
                        ListenAddress = Get-ChildText $s 'listen-address'; ListenPort = Get-ChildText $s 'listen-port'
                        SslListenPort = if ($ssl) { $ssl.InnerText } else { '' }
                        Cluster = Get-ChildText $s 'cluster'; Machine = Get-ChildText $s 'machine'
                        StuckThreadMaxTime = if ($stuck) { $stuck.InnerText } else { '(default 600)' }
                    }
                }
                $clusters = foreach ($c in $x.SelectNodes("//*[local-name()='cluster']")) {
                    $cn = Get-ChildText $c 'name'
                    if ($cn) { [pscustomobject]@{ Domain = $dom.Name; Cluster = $cn; ClusterAddress = Get-ChildText $c 'cluster-address' } }
                }
                $machines = foreach ($m in $x.SelectNodes("//*[local-name()='machine' or local-name()='unix-machine']")) {
                    $nmNode = $m.SelectSingleNode("*[local-name()='node-manager']")
                    if ($nmNode) {
                        [pscustomobject]@{ Domain = $dom.Name; Machine = Get-ChildText $m 'name'
                            NodeManagerAddress = Get-ChildText $nmNode 'listen-address'; NodeManagerPort = Get-ChildText $nmNode 'listen-port'; NodeManagerType = Get-ChildText $nmNode 'nm-type' }
                    }
                }
                Save-Table "03_weblogic_servers_$($dom.Name)"  $wlsServers
                Save-Table "03_weblogic_clusters_$($dom.Name)" $clusters
                Save-Table "03_weblogic_machines_$($dom.Name)" $machines
                foreach ($s in $wlsServers) { Note ("  WLS server {0,-25} port {1,-6} ssl {2,-6} cluster {3,-20} admin={4} stuckThread={5}" -f $s.Server, $s.ListenPort, $s.SslListenPort, $s.Cluster, $s.IsAdminServer, $s.StuckThreadMaxTime) }
            }
            Invoke-Part "JDBC ($($dom.Name))" {
                $jdbc = foreach ($j in (Get-ChildItem (Join-Path $dom.FullName 'config\jdbc') -Filter *.xml -ErrorAction SilentlyContinue)) {
                    [xml] $jx = Get-Content $j.FullName -Raw
                    $url = $jx.SelectSingleNode("//*[local-name()='url']")
                    [pscustomobject]@{ Domain = $dom.Name; DataSource = Get-ChildText $jx.DocumentElement 'name'; Url = if ($url) { $url.InnerText } else { '' } }
                }
                Save-Table "03_jdbc_datasources_$($dom.Name)" $jdbc
            }
            Invoke-Part "Node Manager ($($dom.Name))" {
                $nmp = Join-Path $dom.FullName 'nodemanager\nodemanager.properties'
                if (Test-Path $nmp) {
                    $nm = Select-String -Path $nmp -Pattern '^(CrashRecoveryEnabled|ListenPort|ListenAddress|SecureListener|StartScriptEnabled|NativeVersionEnabled)\s*=' |
                        ForEach-Object { [pscustomobject]@{ Domain = $dom.Name; Setting = $_.Line } }
                    Save-Table "03_nodemanager_settings_$($dom.Name)" $nm
                    foreach ($n in $nm) { Note "  NodeManager ($($dom.Name)): $($n.Setting)" }
                }
            }
        }

        # EPM / OHS instances: every folder under user_projects other than 'domains'
        foreach ($inst in (Get-ChildItem $up.FullName -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne 'domains' })) {
            Invoke-Part "start/stop scripts ($($inst.Name))" {
                foreach ($bat in @('start.bat', 'stop.bat')) {
                    $p = Join-Path $inst.FullName "bin\$bat"
                    if (Test-Path $p) { Save-Text ("03_{0}_{1}" -f $inst.Name, ($bat -replace '\.bat$', '')) (Get-Content $p); Note "  Found $($inst.Name)\bin\$bat" }
                }
            }
            Invoke-Part "reports ($($inst.Name))" {
                $reports = Get-ChildItem (Join-Path $inst.FullName 'diagnostics\reports') -File -ErrorAction SilentlyContinue |
                    Select-Object @{ n = 'Instance'; e = { $inst.Name } }, Name, LastWriteTime, Length
                if ($reports) { Save-Table "03_epm_reports_$($inst.Name)" $reports; Note "  $($inst.Name): $(@($reports).Count) EPM reports - open the newest deployment/validation report in a browser" }
            }
            Invoke-Part "log folders ($($inst.Name))" {
                $logs = foreach ($lf in (Get-ChildItem (Join-Path $inst.FullName 'diagnostics\logs') -Directory -ErrorAction SilentlyContinue)) {
                    $latest = Get-ChildItem $lf.FullName -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
                    [pscustomobject]@{ Instance = $inst.Name; LogFolder = $lf.Name; LatestFile = if ($latest) { $latest.Name } else { '(in subfolders)' }; LatestWrite = if ($latest) { $latest.LastWriteTime } else { $null } }
                }
                Save-Table "03_epm_log_folders_$($inst.Name)" $logs
            }
            Invoke-Part "OHS config ($($inst.Name))" {
                $httpCfg = Join-Path $inst.FullName 'httpConfig'
                if (-not (Test-Path $httpCfg)) { return }
                $ohs = foreach ($f in (Find-Files $httpCfg @('mod_wl_ohs.conf', 'httpd.conf', 'ssl.conf') 10)) {
                    $pattern = if ($f -like '*mod_wl_ohs.conf') { '^\s*[^#\s]' } else { '^\s*Listen\s' }
                    Select-String -Path $f -Pattern $pattern | ForEach-Object { [pscustomobject]@{ File = [IO.Path]::GetFileName($f); Line = $_.Line.Trim() } }
                }
                Save-Table "03_ohs_config_$($inst.Name)" $ohs
                Note "  OHS config lines captured for $($inst.Name): $(@($ohs).Count)"
            }
        }
    }
}

# ---------------------------------------------------------------- 4. scheduled tasks   [Q25 Q35 Q36]
Invoke-Section 'Scheduled tasks (non-Microsoft)' {
    $tasks = Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | ForEach-Object {
        $info = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
        [pscustomobject]@{
            TaskName       = $_.TaskName
            TaskPath       = $_.TaskPath
            State          = $_.State
            Actions        = ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join ' | '
            Triggers       = ($_.Triggers | ForEach-Object { $_.CimClass.CimClassName -replace 'MSFT_Task', '' -replace 'Trigger', '' }) -join ', '
            LastRunTime    = $info.LastRunTime
            LastTaskResult = $info.LastTaskResult
            NextRunTime    = $info.NextRunTime
        }
    }
    Save-Table '04_scheduled_tasks' $tasks
    Note ("Non-Microsoft scheduled tasks: {0}" -f @($tasks).Count)
}

# ---------------------------------------------------------------- 5. service stop / crash history   [Q22 Q36 Q40]
Invoke-Section "Service stop/crash events, last $EventDays days" {
    $names = @($script:EpmServices | ForEach-Object { $_.DisplayName })
    if ($names.Count -eq 0) { Note 'No EPM services on this server; event history skipped.'; return }
    $events = Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Service Control Manager'; Id = 7031, 7034, 7036; StartTime = (Get-Date).AddDays(-$EventDays) } -ErrorAction SilentlyContinue |
        Where-Object { $m = $_.Message; @($names | Where-Object { $m -like "*$_*" }).Count -gt 0 } |
        Select-Object TimeCreated, Id, @{ n = 'Message'; e = { ($_.Message -split "`r?`n")[0] } }
    Save-Table '05_service_events' $events
    $crashes = @($events | Where-Object { $_.Id -in 7031, 7034 })
    Note ("Service state changes: {0}; unexpected terminations (7031/7034): {1}" -f @($events).Count, $crashes.Count)
    # A zero only means "no restarts" if the System log actually reaches back that far
    $oldest = Get-WinEvent -LogName System -MaxEvents 1 -Oldest -ErrorAction SilentlyContinue
    if ($oldest) { Note ("  System event log covers {0:yyyy-MM-dd} onwards ({1} days)" -f $oldest.TimeCreated, [int]((Get-Date) - $oldest.TimeCreated).TotalDays) }
}

# ---------------------------------------------------------------- 6. volumes   [Q14]
Invoke-Section 'Disk volumes' {
    try {
        # Get-Volume usually needs an elevated session
        $vols = Get-Volume | Where-Object { $_.DriveLetter } |
            Select-Object DriveLetter, FileSystemLabel,
                @{ n = 'SizeGB'; e = { [math]::Round($_.Size / 1GB, 1) } },
                @{ n = 'FreeGB'; e = { [math]::Round($_.SizeRemaining / 1GB, 1) } },
                @{ n = 'FreePct'; e = { if ($_.Size) { [math]::Round(100 * $_.SizeRemaining / $_.Size, 1) } else { $null } } }
    } catch {
        # Fallback that works for standard users
        $vols = Get-PSDrive -PSProvider FileSystem | Where-Object { $null -ne $_.Used } |
            Select-Object @{ n = 'DriveLetter'; e = { $_.Name } }, @{ n = 'FileSystemLabel'; e = { $_.Description } },
                @{ n = 'SizeGB'; e = { [math]::Round(($_.Used + $_.Free) / 1GB, 1) } },
                @{ n = 'FreeGB'; e = { [math]::Round($_.Free / 1GB, 1) } },
                @{ n = 'FreePct'; e = { if (($_.Used + $_.Free) -gt 0) { [math]::Round(100 * $_.Free / ($_.Used + $_.Free), 1) } else { $null } } }
        Note 'Disk volumes: Get-Volume denied, used Get-PSDrive instead.'
    }
    Save-Table '06_volumes' $vols
}

# ---------------------------------------------------------------- 7. SQL Server / clustering   [Q12 Q13]
Invoke-Section 'SQL Server instance and high availability' {
    $instKey = 'HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\Instance Names\SQL'
    if (-not (Test-Path $instKey)) { Note 'No SQL Server instance on this server.'; return }
    $rows = @()
    $inst = Get-ItemProperty $instKey
    foreach ($p in ($inst.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' })) {
        $id = $p.Value
        $hadr = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\$id\MSSQLServer\HADR" -ErrorAction SilentlyContinue
        $setup = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\$id\Setup" -ErrorAction SilentlyContinue
        $rows += [pscustomobject]@{ Instance = $p.Name; InstanceId = $id; Edition = $setup.Edition; Version = $setup.Version
            AlwaysOnEnabled = if ($hadr) { [bool]$hadr.HADR_Enabled } else { $false } }
    }
    $clus = Get-Service -Name ClusSvc -ErrorAction SilentlyContinue
    Save-Table '07_sql_instances' $rows
    foreach ($r in $rows) { Note "  SQL instance $($r.Instance) $($r.Edition) $($r.Version) AlwaysOn=$($r.AlwaysOnEnabled)" }
    Note ("  Failover cluster service: {0}" -f $(if ($clus) { $clus.Status } else { 'not installed (standalone)' }))
}

# ---------------------------------------------------------------- 8. New Relic   [Q21 Q47]
Invoke-Section 'New Relic agent' {
    $nrDir = 'C:\Program Files\New Relic\newrelic-infra'
    if (-not (Test-Path $nrDir)) { Note 'New Relic infrastructure agent not found on this server.'; return }
    $cfg = Join-Path $nrDir 'newrelic-infra.yml'
    if (Test-Path $cfg) {
        # never copy the licence key
        Save-Text '08_newrelic_config' (Get-Content $cfg | Where-Object { $_ -notmatch 'license_key|api_key|password|secret' })
    }
    $integrations = Get-ChildItem (Join-Path $nrDir 'integrations.d') -File -ErrorAction SilentlyContinue | Select-Object Name, LastWriteTime
    Save-Table '08_newrelic_integrations' $integrations
    # Windows services integration: which services New Relic already watches
    $ws = Join-Path $nrDir 'integrations.d\winservices-config.yml'
    if (Test-Path $ws) {
        Save-Text '08_newrelic_winservices' (Get-Content $ws | Where-Object { $_ -notmatch 'license_key|api_key|password|secret|token' })
        Note '  New Relic Windows-services integration configured (see 08_newrelic_winservices.txt)'
    }
    Note ("New Relic agent present; integrations configured: {0}" -f @($integrations).Count)
}

# ---------------------------------------------------------------- 9. reachability from this host   [Q16 Q17]
if ($NodesFile) {
    Invoke-Section 'Reachability of EPM nodes from this host' {
        $map = Import-Csv $NodesFile
        $nodes = $map | Where-Object { $_.Placeholder -like '*NODE*' } | Sort-Object Placeholder -Unique
        $ports = @(135, 445, 1433, 5985, 5986) + $ExtraPorts | Sort-Object -Unique
        $result = foreach ($n in $nodes) {
            $row = [ordered]@{ Node = $n.Placeholder }
            foreach ($p in $ports) {
                $t = Test-NetConnection -ComputerName $n.Value -Port $p -WarningAction SilentlyContinue -InformationLevel Quiet
                $row["Port$p"] = $t
            }
            $row['WinRM']         = [bool](Test-WSMan -ComputerName $n.Value -ErrorAction SilentlyContinue)
            $row['RemoteSvcQuery'] = try {
                $c = @(Get-CimInstance Win32_Service -ComputerName $n.Value -Filter "DisplayName LIKE '%Oracle%' OR DisplayName LIKE '%Hyperion%' OR Name LIKE 'MSSQL%'" -OperationTimeoutSec 20)
                "OK ($($c.Count) services visible)"
            } catch { 'Denied/failed: ' + $_.Exception.Message.Split([Environment]::NewLine)[0] }
            [pscustomobject]$row
        }
        Save-Table '09_reachability' $result   # uses placeholders only
        foreach ($r in $result) { Note ("  {0}: WinRM={1} RemoteSvcQuery={2}" -f $r.Node, $r.WinRM, $r.RemoteSvcQuery) }
    }
}

# ---------------------------------------------------------------- 10. user-facing URL without logging in   [Q05 Q17 Q20]
if ($LbUrl) {
    Invoke-Section 'Load balancer / Workspace page (no login)' {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $probe = foreach ($path in @('/workspace/index.jsp', '/workspace/', '/hfmadf/', '/hr/')) {
            $url = $LbUrl.TrimEnd('/') + $path
            $sw = [Diagnostics.Stopwatch]::StartNew()
            try {
                $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 30 -MaximumRedirection 5
                [pscustomobject]@{ Path = $path; Status = $r.StatusCode; Ms = $sw.ElapsedMilliseconds; Note = '' }
            } catch {
                $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { $null }
                [pscustomobject]@{ Path = $path; Status = $code; Ms = $sw.ElapsedMilliseconds; Note = $_.Exception.Message.Split([Environment]::NewLine)[0] }
            }
        }
        Save-Table '10_url_probe' $probe
        foreach ($p in $probe) { Note ("  {0,-22} HTTP {1} in {2} ms {3}" -f $p.Path, $p.Status, $p.Ms, $p.Note) }
    }
}

# ---------------------------------------------------------------- 11. masked copy for sharing (always written)
Note "Done. Raw output (contains real names) is in the folder this masked copy sits in."
Invoke-Section 'Masked copy (names, accounts and IPs replaced by placeholders)' {
    $map = @()
    if ($NodesFile) { $map += Import-Csv $NodesFile }
    if ($LbUrl) { $map += [pscustomobject]@{ Placeholder = '{{LB_URL}}'; Value = ([uri]$LbUrl).Host } }
    # service accounts (user@domain or DOMAIN\user) and their domains
    foreach ($acct in ($script:EpmServices | ForEach-Object { $_.StartName } | Where-Object { $_ -and $_ -notmatch '^(LocalSystem|NT AUTHORITY|NT SERVICE)' } | Sort-Object -Unique)) {
        $map += [pscustomobject]@{ Placeholder = '{{SERVICE_ACCOUNT}}'; Value = $acct }
        if ($acct -match '@(.+)$') { $map += [pscustomobject]@{ Placeholder = '{{AD_DOMAIN}}'; Value = $Matches[1] } }
        if ($acct -match '^([^\\]+)\\') { $map += [pscustomobject]@{ Placeholder = '{{AD_DOMAIN}}'; Value = $Matches[1] } }
    }
    $map += [pscustomobject]@{ Placeholder = '{{THIS_HOST}}'; Value = $env:COMPUTERNAME }
    $map += [pscustomobject]@{ Placeholder = '{{USER}}'; Value = $env:USERNAME }
    if ($env:USERDNSDOMAIN) { $map += [pscustomobject]@{ Placeholder = '{{AD_DOMAIN}}'; Value = $env:USERDNSDOMAIN } }
    if ($env:USERDOMAIN)    { $map += [pscustomobject]@{ Placeholder = '{{AD_DOMAIN}}'; Value = $env:USERDOMAIN } }
    $map = @($map | Where-Object { $_.Value -and $_.Value.Length -ge 3 } | Sort-Object { $_.Value.Length } -Descending)   # longest first

    $maskDir = Join-Path $OutDir 'masked'
    New-Item -ItemType Directory -Path $maskDir -Force | Out-Null
    foreach ($f in Get-ChildItem $OutDir -File | Where-Object { $_.Name -notlike '02_listening_ports*' }) {   # ports stay on the server
        $t = Get-Content $f.FullName -Raw
        if ($null -eq $t) { $t = '' }
        foreach ($m in $map) { $t = $t -replace ("(?<![A-Za-z0-9])" + [regex]::Escape($m.Value) + "(?![A-Za-z0-9])"), $m.Placeholder }   # whole names; _ . - count as separators
        # Other servers' names this host does not know about:
        $t = $t -replace '(?i)(sqlserver://|jdbc:[a-z]+:[a-z]*:?//)([^:;/\s"]+)', '$1{{DB_HOST}}'          # database hosts in connection strings
        $t = $t -replace '(?<![A-Za-z0-9{}])[A-Za-z0-9][A-Za-z0-9-]*(?=\.\{\{AD_DOMAIN\}\})', '{{HOST}}'   # anything.<domain>
        $t = [regex]::Replace($t, '(?im)^.*web[lL]ogic(Cluster|Host)\b.*$', {
                 param($line) $line.Value -replace '(?<=[\s,])(?!\{\{)[A-Za-z0-9][A-Za-z0-9.-]*(?=:\d{2,5})', '{{HOST}}' })   # OHS routing targets
        $t = $t -replace '(?i)(xfm-odl-srv-)[^-\s"]+(-)', '$1{{HFM_SERVER_NAME}}$2'                          # HFM log names carry a server name
        $t = $t -replace '\b(?:\d{1,3}\.){3}\d{1,3}\b', '{{IP}}'    # also masks 4-part version numbers
        Set-Content -Path (Join-Path $maskDir $f.Name) -Value $t -Encoding UTF8
    }
    # One combined text file (and the clipboard) so the output can leave the server as a single paste
    $parts = foreach ($f in (Get-ChildItem $maskDir -File | Where-Object { $_.Name -ne '00_ALL_IN_ONE.txt' } | Sort-Object Name)) {
        "===== $($f.Name) ====="
        Get-Content $f.FullName
        ''
    }
    $header = @("HFM DISCOVERY (masked) - run at $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
                'Paste this whole text back for analysis. Review it first: service accounts and paths are masked, but check nothing sensitive remains.', '')
    $allText = ($header + $parts) -join "`r`n"
    $allFile = Join-Path $maskDir '00_ALL_IN_ONE.txt'
    Set-Content -Path $allFile -Value $allText -Encoding UTF8
    Write-Host "Masked output combined into one file: $allFile ($([math]::Round($allText.Length / 1KB, 1)) KB)" -ForegroundColor Green
    if (-not $NoClipboard) {
        try {
            Set-Clipboard -Value $allText
            Write-Host 'Combined masked text copied to the clipboard - paste it on your machine.' -ForegroundColor Green
        } catch {
            Write-Host "Clipboard not available here. Open the file in Notepad, press Ctrl+A, Ctrl+C:  notepad `"$allFile`"" -ForegroundColor Yellow
        }
    }
}
