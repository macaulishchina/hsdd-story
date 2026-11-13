param(
    [switch] $Uninstall,
    [string] $Workspace,
    [ValidateRange(1, 65535)]
    [int] $Port = 8765
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = (Get-Item -LiteralPath $PSScriptRoot).FullName
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$hash = [Security.Cryptography.SHA256]::Create()
try {
    $key = $root.ToUpperInvariant() + '|' + $identity.User.Value
    $digest = $hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($key))
    $taskName = 'HSDD Story ' + [BitConverter]::ToString($digest, 0, 8).Replace('-', '')
} finally {
    $hash.Dispose()
}
$taskId = @{ TaskPath = '\'; TaskName = $taskName }
$managed = Join-Path $root '.hsdd-service'
$marker = Join-Path $managed 'owner.txt'
$description = "Managed by HSDD Story deployment in $root"

function Get-ManagedTask {
    $task = Get-ScheduledTask @taskId -ErrorAction SilentlyContinue
    if ($task -and $task.Description -cne $description) {
        throw "Task '$taskName' belongs to another installation."
    }
    return $task
}

function Test-ManagedDirectory {
    $item = Get-Item -LiteralPath $managed -Force -ErrorAction SilentlyContinue
    if (-not $item) { return $false }
    $owner = Get-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
    $link = [IO.FileAttributes]::ReparsePoint
    if (-not $item.PSIsContainer -or ($item.Attributes -band $link) -or
            -not $owner -or $owner.PSIsContainer -or ($owner.Attributes -band $link) -or
            (Get-Content -LiteralPath $marker -Raw).Trim() -cne $taskName) {
        throw "Deployment directory '$managed' has no matching ownership marker."
    }
    return $true
}

function Remove-ManagedDirectory {
    param([string] $Path)
    $item = Get-Item -LiteralPath $Path -Force
    $inside = $item.FullName.StartsWith($managed + '\', [StringComparison]::OrdinalIgnoreCase)
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            ($item.FullName -ne $managed -and -not $inside)) {
        throw "Refusing to remove a directory outside the deployment: '$Path'."
    }
    [IO.Directory]::Delete($item.FullName, $true)
}

function Invoke-Python {
    param([string] $Executable, [string[]] $Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed (exit $LASTEXITCODE): $($Arguments -join ' ')"
    }
}

function Wait-PortAvailable {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    $listener.ExclusiveAddressUse = $true
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try {
            $listener.Start()
            return
        } catch [Net.Sockets.SocketException] {
            Start-Sleep -Milliseconds 200
        } finally {
            $listener.Stop()
        }
    }
    throw "Port $Port is unavailable. Stop the process using it or specify another -Port."
}

function Wait-ServiceReady {
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        try {
            $request = [Net.WebRequest]::Create("http://127.0.0.1:$Port/api/schema")
            $request.Proxy = $null
            $request.Timeout = 1000
            $response = $request.GetResponse()
            try {
                $reader = [IO.StreamReader]::new($response.GetResponseStream())
                try { $schema = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
                if ($schema.nodeTypes.acceptance -and (Get-ManagedTask).State -eq 'Running') { return }
            } finally {
                $response.Dispose()
            }
        } catch [Net.WebException] {
            # The service may still be binding its listener.
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    $info = Get-ScheduledTaskInfo @taskId
    throw "Service did not start on port $Port. Task result: $($info.LastTaskResult). Check whether another service is using the workspace."
}

$mutex = [Threading.Mutex]::new($false, "Local\$taskName")
$locked = $false
try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another deployment or uninstall is already running.' }

    $hasManagedDirectory = Test-ManagedDirectory
    $existing = Get-ManagedTask
    if ($Uninstall) {
        if ($existing) {
            Disable-ScheduledTask @taskId | Out-Null
            Stop-ScheduledTask @taskId
            Unregister-ScheduledTask @taskId -Confirm:$false
        }
        if ($hasManagedDirectory) {
            Remove-ManagedDirectory $managed
        }
        Write-Host 'HSDD Story service is uninstalled. Workspace data was kept.'
        return
    }

    $useDemo = -not $Workspace
    if ($useDemo) { $Workspace = Join-Path $root '.demo' }
    $Workspace = [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Workspace))
    if ($Workspace.TrimEnd('\').Equals($managed, [StringComparison]::OrdinalIgnoreCase) -or
            $Workspace.StartsWith($managed + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Choose a workspace outside the managed installation directory.'
    }
    if (-not $useDemo -and -not (Test-Path -LiteralPath $Workspace -PathType Container)) {
        throw "Workspace directory does not exist: '$Workspace'."
    }

    $wasRunning = $existing -and $existing.State -eq 'Running'
    if (-not $wasRunning -or -not ($existing.Actions.Arguments -match " --port $Port$")) {
        Wait-PortAvailable
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    $checkVersion = 'import sys; raise SystemExit(sys.version_info < (3, 12))'
    if ($python) { & $python.Source -c $checkVersion }
    if ($python -and $LASTEXITCODE -eq 0) {
        $bootstrap = $python.Source
        $bootstrapArgs = @()
    } elseif ($launcher) {
        $bootstrap = $launcher.Source
        $bootstrapArgs = @('-3')
        Invoke-Python $bootstrap ($bootstrapArgs + @('-c', $checkVersion))
    } else {
        throw 'Install Python 3.12 or newer, then run deploy.cmd again.'
    }

    if (-not $hasManagedDirectory) {
        New-Item -ItemType Directory -Path $managed | Out-Null
        Set-Content -LiteralPath $marker -Value $taskName -Encoding Ascii
    }
    # Prepare the new environment while the previous deployment can still run.
    $runtime = Join-Path $managed ('runtime-' + [Guid]::NewGuid().ToString('N'))
    $executable = Join-Path $runtime 'Scripts\python.exe'
    $previousXml = $null
    $taskChanged = $false
    try {
        Invoke-Python $bootstrap ($bootstrapArgs + @('-m', 'venv', $runtime))
        Invoke-Python $executable @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', $root)
        $story = Join-Path $Workspace '.hsdd\story.xml'
        if ($useDemo -and -not (Test-Path -LiteralPath $story)) {
            Invoke-Python $executable @('-m', 'hsdd_story', 'demo', '--workspace', $Workspace)
        }

        if ($existing) {
            $previousXml = Export-ScheduledTask @taskId
            $taskChanged = $true
            Disable-ScheduledTask @taskId | Out-Null
            Stop-ScheduledTask @taskId
        }
        Wait-PortAvailable
        $quotedWorkspace = '"' + ($Workspace -replace '\\+$', '$0$0') + '"'
        $action = New-ScheduledTaskAction -Execute $executable -Argument "-m hsdd_story serve --workspace $quotedWorkspace --port $Port" -WorkingDirectory $managed
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
        $principal = New-ScheduledTaskPrincipal -UserId $identity.User.Value -LogonType Interactive -RunLevel Limited
        $settingsArgs = @{
            ExecutionTimeLimit = [TimeSpan]::Zero
            MultipleInstances = 'IgnoreNew'
            RestartCount = 3
            RestartInterval = (New-TimeSpan -Minutes 1)
            AllowStartIfOnBatteries = $true
            DontStopIfGoingOnBatteries = $true
        }
        $settings = New-ScheduledTaskSettingsSet @settingsArgs
        $taskChanged = $true
        Register-ScheduledTask @taskId -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $description -Force | Out-Null
        Start-ScheduledTask @taskId
        Wait-ServiceReady
    } catch {
        $failure = $_
        if ($taskChanged) {
            $current = Get-ManagedTask
            if ($current) {
                Disable-ScheduledTask @taskId | Out-Null
                Stop-ScheduledTask @taskId
            }
            if ($previousXml) {
                Register-ScheduledTask @taskId -Xml $previousXml -Force | Out-Null
                if ($wasRunning) { Start-ScheduledTask @taskId }
            } elseif ($current) {
                Unregister-ScheduledTask @taskId -Confirm:$false
            }
        }
        if (Test-Path -LiteralPath $runtime) {
            Remove-ManagedDirectory $runtime
        }
        throw $failure
    }

    Get-ChildItem -LiteralPath $managed -Directory -Force | Where-Object {
        $_.Name -match '^runtime-[a-f0-9]{32}$' -and $_.FullName -ne $runtime -and
            -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
    } | ForEach-Object {
        try { Remove-ManagedDirectory $_.FullName } catch { Write-Warning $_.Exception.Message }
    }

    Write-Host "HSDD Story is running. Task: $taskName"
    Write-Host "Workspace: $Workspace"
    Write-Host "Web: http://127.0.0.1:$Port/"
    Write-Host "MCP: http://127.0.0.1:$Port/mcp"
    Write-Host "Connect a Streamable HTTP MCP client to the MCP URL above. Call story_guide for workflow help."
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
