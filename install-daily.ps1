# Register the current user's local updater, with no stored password.
$ErrorActionPreference = 'Stop'
$name = 'IndustryMACD-DailyClose-18916'
$description = 'Refresh local industry MACD closes at 16:15 Asia/Shanghai.'
$principal = [Security.Principal.WindowsIdentity]::GetCurrent()
$userSid = $principal.User.Value
$root = $PSScriptRoot
$entry = Join-Path $root 'app.py'
if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) { throw "Missing app.py" }

$python = $null
foreach ($candidate in @('python.exe', 'py.exe')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    $pythonArgs = @()
    if ($candidate -eq 'py.exe') { $pythonArgs += '-3' }
    $pythonArgs += @('-c', "import sys,json; assert sys.version_info >= (3,9); print(json.dumps(sys.executable))")
    $resolved = & $cmd.Source @pythonArgs 2>$null
    if ($LASTEXITCODE -eq 0 -and $resolved) {
        $python = $resolved | ConvertFrom-Json
        break
    }
}
if (-not $python) { throw "Python 3.9+ is needed for the daily update." }
$windowless = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (Test-Path -LiteralPath $windowless -PathType Leaf) { $python = $windowless }

$existing = Get-ScheduledTask -TaskPath '\' -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $description) {
    throw "A different scheduled task already uses the name $name."
}
$start = [DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(8)).ToString('yyyy-MM-dd') + 'T16:15:00+08:00'
$command = [Security.SecurityElement]::Escape($python)
$folder = [Security.SecurityElement]::Escape($root)
$arguments = [Security.SecurityElement]::Escape(('"{0}" due' -f $entry))
$definition = @"
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$description</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger><StartBoundary>$start</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>
    <LogonTrigger><Enabled>true</Enabled><UserId>$userSid</UserId><Delay>PT1M</Delay></LogonTrigger>
  </Triggers>
  <Principals><Principal id="LocalUser"><UserId>$userSid</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable><WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT30M</Interval><Count>3</Count></RestartOnFailure>
  </Settings>
  <Actions Context="LocalUser"><Exec><Command>$command</Command><Arguments>$arguments</Arguments><WorkingDirectory>$folder</WorkingDirectory></Exec></Actions>
</Task>
"@
Register-ScheduledTask -TaskName $name -TaskPath '\' -Xml $definition -Force | Out-Null
$task = Get-ScheduledTask -TaskName $name -TaskPath '\'
if ($task.Actions[0].Execute -ne $python -or $task.Actions[0].WorkingDirectory -ne $root) {
    throw "Could not verify the updater action."
}
Start-ScheduledTask -TaskName $name -TaskPath '\'
$next = (Get-ScheduledTaskInfo -TaskName $name -TaskPath '\').NextRunTime
Write-Host "Registered: $name"
Write-Host "Scheduled: 16:15 Asia/Shanghai; Next run (local time): $next"
Write-Host "A catch-up check is running now. The browser can be closed."
