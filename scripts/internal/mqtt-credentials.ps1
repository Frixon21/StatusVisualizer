# Shared MQTT password prompt + broker login check for the deployment package.
$ErrorActionPreference = "Stop"

function ConvertFrom-SecureStringPlain {
    param([Parameter(Mandatory = $true)][SecureString]$SecurePassword)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePassword)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Test-MqttPasswordMissing {
    param([AllowNull()][string]$Password)
    if ($null -eq $Password) { return $true }
    $trimmed = $Password.Trim()
    if (-not $trimmed) { return $true }
    if ($trimmed -eq "REPLACE_ME") { return $true }
    if ($trimmed -like "replace*") { return $true }
    return $false
}

function Read-MqttPasswordFromUser {
    param([string]$Username = "")
    $who = if ($Username) { " for user '$Username'" } else { "" }
    Write-Host "Enter MQTT broker password$who (input hidden):"
    $secure = Read-Host -AsSecureString
    if (-not $secure -or $secure.Length -eq 0) {
        throw "Password is required."
    }
    return ConvertFrom-SecureStringPlain -SecurePassword $secure
}

function Encode-MqttUtf8String {
    param([Parameter(Mandatory = $true)][string]$Value)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    if ($bytes.Length -gt 65535) { throw "MQTT string is too long." }
    $out = New-Object byte[] ($bytes.Length + 2)
    $out[0] = [byte](($bytes.Length -shr 8) -band 0xFF)
    $out[1] = [byte]($bytes.Length -band 0xFF)
    [Array]::Copy($bytes, 0, $out, 2, $bytes.Length)
    return , $out
}

function Encode-MqttRemainingLength {
    param([Parameter(Mandatory = $true)][int]$Length)
    $bytes = New-Object Collections.Generic.List[byte]
    $x = $Length
    do {
        $digit = $x % 128
        $x = [int][Math]::Floor($x / 128)
        if ($x -gt 0) { $digit = $digit -bor 0x80 }
        $bytes.Add([byte]$digit)
    } while ($x -gt 0)
    return , $bytes.ToArray()
}

function New-MqttConnectPacket {
    param(
        [Parameter(Mandatory = $true)][string]$ClientId,
        [string]$Username = "",
        [string]$Password = "",
        [int]$KeepAliveSeconds = 30
    )
    $protocolName = [byte[]](Encode-MqttUtf8String "MQTT")
    $variable = New-Object Collections.Generic.List[byte]
    $variable.AddRange($protocolName)
    $variable.Add([byte]4) # MQTT 3.1.1
    $flags = 0x02 # clean session
    if ($Username) { $flags = $flags -bor 0x80 }
    if ($Password) { $flags = $flags -bor 0x40 }
    $variable.Add([byte]$flags)
    $variable.Add([byte](($KeepAliveSeconds -shr 8) -band 0xFF))
    $variable.Add([byte]($KeepAliveSeconds -band 0xFF))

    $payload = New-Object Collections.Generic.List[byte]
    $payload.AddRange([byte[]](Encode-MqttUtf8String $ClientId))
    if ($Username) { $payload.AddRange([byte[]](Encode-MqttUtf8String $Username)) }
    if ($Password) { $payload.AddRange([byte[]](Encode-MqttUtf8String $Password)) }

    $remaining = $variable.Count + $payload.Count
    $packet = New-Object Collections.Generic.List[byte]
    $packet.Add([byte]0x10) # CONNECT
    $packet.AddRange([byte[]](Encode-MqttRemainingLength $remaining))
    $packet.AddRange($variable.ToArray())
    $packet.AddRange($payload.ToArray())
    return , $packet.ToArray()
}

function Test-MqttBrokerLogin {
    param(
        [Parameter(Mandatory = $true)][string]$HostName,
        [Parameter(Mandatory = $true)][int]$Port,
        [string]$Username = "",
        [string]$Password = "",
        [bool]$UseTls = $false,
        [int]$TimeoutMs = 10000
    )
    $clientId = "sv-check-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
    $packet = New-MqttConnectPacket -ClientId $clientId -Username $Username -Password $Password
    $tcp = New-Object Net.Sockets.TcpClient
    $ssl = $null
    try {
        $connectTask = $tcp.ConnectAsync($HostName, $Port)
        if (-not $connectTask.Wait($TimeoutMs)) {
            throw "Timed out connecting to ${HostName}:${Port}."
        }
        if ($connectTask.IsFaulted) {
            throw "Unable to connect to ${HostName}:${Port}: $($connectTask.Exception.InnerException.Message)"
        }
        $network = $tcp.GetStream()
        $stream = [IO.Stream]$network
        if ($UseTls) {
            $ssl = New-Object Net.Security.SslStream($network, $false)
            $auth = $ssl.AuthenticateAsClientAsync($HostName)
            if (-not $auth.Wait($TimeoutMs)) { throw "TLS handshake timed out." }
            if ($auth.IsFaulted) {
                throw "TLS handshake failed: $($auth.Exception.InnerException.Message)"
            }
            $stream = $ssl
        }
        $stream.ReadTimeout = $TimeoutMs
        $stream.WriteTimeout = $TimeoutMs
        $stream.Write($packet, 0, $packet.Length)
        $stream.Flush()

        $header = New-Object byte[] 2
        $read = $stream.Read($header, 0, 2)
        if ($read -lt 2) { throw "Broker closed the connection before CONNACK." }
        if ($header[0] -ne 0x20) {
            throw ("Unexpected MQTT response type 0x{0:X2}." -f $header[0])
        }
        # Remaining length is usually 2 for CONNACK; read encoded length then body.
        $multiplier = 1
        $remaining = 0
        $digit = $header[1]
        while ($true) {
            $remaining += ($digit -band 0x7F) * $multiplier
            if (($digit -band 0x80) -eq 0) { break }
            $multiplier *= 128
            if ($multiplier -gt 128 * 128 * 128) { throw "Invalid MQTT remaining length." }
            $next = $stream.ReadByte()
            if ($next -lt 0) { throw "Broker closed during CONNACK." }
            $digit = $next
        }
        if ($remaining -lt 2) { throw "CONNACK was too short." }
        $body = New-Object byte[] $remaining
        $got = 0
        while ($got -lt $remaining) {
            $n = $stream.Read($body, $got, $remaining - $got)
            if ($n -le 0) { throw "Broker closed while reading CONNACK." }
            $got += $n
        }
        $returnCode = [int]$body[1]
        switch ($returnCode) {
            0 { return $true }
            1 { throw "Broker rejected connection: unacceptable protocol version." }
            2 { throw "Broker rejected connection: identifier rejected." }
            3 { throw "Broker unavailable." }
            4 { throw "Wrong MQTT username or password." }
            5 { throw "MQTT user is not authorized." }
            default { throw "Broker rejected connection (code $returnCode)." }
        }
    } finally {
        if ($ssl) { $ssl.Dispose() }
        $tcp.Close()
    }
}

function Get-JsonPropertyValue {
    param(
        [Parameter(Mandatory = $true)]$Object,
        [Parameter(Mandatory = $true)][string[]]$Path
    )
    $current = $Object
    foreach ($part in $Path) {
        if ($null -eq $current) { return $null }
        $prop = $current.PSObject.Properties[$part]
        if (-not $prop) { return $null }
        $current = $prop.Value
    }
    return $current
}

function Set-JsonPropertyValue {
    param(
        [Parameter(Mandatory = $true)]$Object,
        [Parameter(Mandatory = $true)][string[]]$Path,
        [Parameter(Mandatory = $true)]$Value
    )
    if ($Path.Count -lt 1) { throw "Path is required." }
    $current = $Object
    for ($i = 0; $i -lt $Path.Count - 1; $i++) {
        $part = $Path[$i]
        $prop = $current.PSObject.Properties[$part]
        if (-not $prop -or $null -eq $prop.Value) {
            $child = [pscustomobject]@{}
            $current | Add-Member -NotePropertyName $part -NotePropertyValue $child -Force
            $current = $child
        } else {
            $current = $prop.Value
        }
    }
    $leaf = $Path[-1]
    $current | Add-Member -NotePropertyName $leaf -NotePropertyValue $Value -Force
}

function Read-MqttJsonConfig {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Configuration not found: $Path"
    }
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding utf8
    return $raw | ConvertFrom-Json
}

function Save-MqttJsonConfig {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Object
    )
    $json = $Object | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

function Resolve-MqttBrokerFields {
    param(
        [Parameter(Mandatory = $true)]$Config,
        [ValidateSet("dashboard", "helper")][string]$Kind
    )
    if ($Kind -eq "helper") {
        $broker = $Config.broker
        if (-not $broker) { throw "config.json is missing broker settings." }
        return [pscustomobject]@{
            Host     = [string]$broker.host
            Port     = [int]$broker.port
            Username = [string]$broker.username
            Password = [string](Get-JsonPropertyValue $Config @("broker", "password"))
            UseTls   = [bool]($broker.tls.enabled)
            PasswordPath = @("broker", "password")
        }
    }
    return [pscustomobject]@{
        Host     = [string]$Config.host
        Port     = [int]$Config.port
        Username = [string]$Config.username
        Password = [string](Get-JsonPropertyValue $Config @("password"))
        UseTls   = [bool]($Config.tls.enabled)
        PasswordPath = @("password")
    }
}

function Get-VerifiedMqttPassword {
    # Prompt, verify against the broker, and return the password without saving it.
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [ValidateSet("dashboard", "helper")][string]$Kind
    )
    $config = Read-MqttJsonConfig -Path $ConfigPath
    $fields = Resolve-MqttBrokerFields -Config $config -Kind $Kind
    if (-not $fields.Host) { throw "Broker host is missing in $ConfigPath" }
    if (-not $fields.Port) { throw "Broker port is missing in $ConfigPath" }

    $password = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $password = Read-MqttPasswordFromUser -Username $fields.Username
        Write-Host "Checking password against $($fields.Host):$($fields.Port)..."
        try {
            Test-MqttBrokerLogin `
                -HostName $fields.Host `
                -Port $fields.Port `
                -Username $fields.Username `
                -Password $password `
                -UseTls:$fields.UseTls | Out-Null
            Write-Host "MQTT password accepted."
            return $password
        } catch {
            $password = $null
            Write-Host "Attempt $attempt failed: $($_.Exception.Message)" -ForegroundColor Red
            if ($attempt -eq 3) {
                throw "MQTT password check failed after 3 attempts."
            }
        }
    }
}
