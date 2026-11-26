[CmdletBinding(DefaultParameterSetName='Package')]
param(
    [Parameter(ParameterSetName='Package')][switch]$PackageOnly,
    [Parameter(Mandatory, ParameterSetName='Deploy')][switch]$Deploy,
    [Parameter(Mandatory, ParameterSetName='Deploy')][guid]$Subscription,
    [Parameter(Mandatory, ParameterSetName='Deploy')][string]$ResourceGroup,
    [Parameter(Mandatory, ParameterSetName='Deploy')][string]$FoundryAccount,
    [Parameter(Mandatory, ParameterSetName='Deploy')][guid]$ApiClientId,
    [Parameter(ParameterSetName='Deploy')][string]$Location = 'eastus',
    [Parameter(ParameterSetName='Deploy')][string]$AnalyzerId = 'contoso-safety',
    [Parameter(ParameterSetName='Deploy')][string]$Mailbox,
    [Parameter(ParameterSetName='Deploy')][string]$TrustedSenders,
    [Parameter(ParameterSetName='Deploy')][uri]$RemediationEndpoint,
    [Parameter(ParameterSetName='Deploy')][string]$RemediationAudience
)
$ErrorActionPreference = 'Stop'
function Invoke-AzChecked([string[]]$Arguments) {
    $result = & az @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed: $($Arguments[0..1] -join ' ')" }
    return $result
}
$build = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('pdf-forms-' + [guid]::NewGuid()))
$functionZip = Join-Path $build.FullName 'functions.zip'
$workflowZip = Join-Path $build.FullName 'workflows.zip'
$functionFiles = @('function_app.py','forms.py','host.json','requirements.txt') | ForEach-Object { Join-Path "$PSScriptRoot/functions" $_ }
Compress-Archive -LiteralPath $functionFiles -DestinationPath $functionZip
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory("$PSScriptRoot/workflows", $workflowZip)
if (-not $Deploy) { Write-Output $build.FullName; return }
if ([bool]$Mailbox -ne [bool]$TrustedSenders) { throw 'Supply both Mailbox and TrustedSenders or neither.' }
if ([bool]$RemediationEndpoint -ne [bool]$RemediationAudience) { throw 'Supply both remediation settings or neither.' }
if ($RemediationEndpoint -and ($RemediationEndpoint.Scheme -ne 'https' -or $RemediationEndpoint.Query -or $RemediationEndpoint.UserInfo)) { throw 'Use an HTTPS remediation endpoint without embedded credentials or query parameters.' }
$target = @('--resource-group', $ResourceGroup, '--subscription', $Subscription.ToString(), '--only-show-errors')
$parameters = @("location=$Location", "foundryAccount=$FoundryAccount", "apiClientId=$ApiClientId", "analyzerId=$AnalyzerId")
if ($Mailbox) { $parameters += @("mailbox=$Mailbox", "trustedSenders=$TrustedSenders") }
if ($RemediationEndpoint) { $parameters += @("remediationEndpoint=$RemediationEndpoint", "remediationAudience=$RemediationAudience") }
$outputs = Invoke-AzChecked (@('deployment','group','create','--name','pdf-forms-modern','--template-file',"$PSScriptRoot/infra/main.bicep",'--parameters') + $parameters + $target + @('--query','properties.outputs','-o','json')) | ConvertFrom-Json
Invoke-AzChecked (@('functionapp','deployment','source','config-zip','--build-remote','true','--src',$functionZip,'--name',$outputs.functionName.value) + $target + @('-o','none'))
Invoke-AzChecked (@('logicapp','deployment','source','config-zip','--src',$workflowZip,'--name',$outputs.workflowName.value) + $target + @('-o','none'))
Invoke-AzChecked (@('deployment','group','create','--name','pdf-forms-events','--template-file',"$PSScriptRoot/infra/events.bicep",'--parameters',"storageName=$($outputs.storageName.value)","workflowName=$($outputs.workflowName.value)") + $target + @('-o','none'))
Write-Output "Upload a sample PDF to raw in $($outputs.storageName.value). Inspect forms/records at $($outputs.cosmosEndpoint.value)."