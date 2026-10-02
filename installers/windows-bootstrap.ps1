# Build source only: shipped as an inline command in Install med-lit.bat.
$ErrorActionPreference = 'Stop';
try {
  if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This installer requires Windows x64.' };
  $Checkout = $env:MED_LIT_INSTALL_DEV;
  if ($Checkout -and -not (Test-Path -LiteralPath (Join-Path $Checkout 'pyproject.toml'))) { throw 'Developer checkout must contain pyproject.toml.' };
  $Runtime = $env:MED_LIT_RUNTIME_DIR;
  if (-not $Runtime) { $Runtime = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'med-lit-mcp\runtime' };
  $Existing = Get-Command uv.exe -ErrorAction SilentlyContinue;
  if ($Existing) { $UvBin = $Existing.Source } elseif (Test-Path -LiteralPath (Join-Path $Runtime 'uv.exe')) { $UvBin = Join-Path $Runtime 'uv.exe' } else {
    New-Item -ItemType Directory -Force -Path $Runtime | Out-Null;
    $Temporary = Join-Path $Runtime ('.download-' + [Guid]::NewGuid().ToString('N'));
    New-Item -ItemType Directory -Path $Temporary | Out-Null;
    try {
      Write-Output 'Installing the med-lit runtime...';
      $Archive = Join-Path $Temporary 'uv.zip';
      [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12;
      Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/download/0.12.21/uv-x86_64-pc-windows-msvc.zip' -OutFile $Archive -UseBasicParsing -TimeoutSec 180;
      if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant() -ne '5d223efa0bf00208c3853246af09420419dfbd352536aa6bb8163d6170e23890') { throw 'Runtime checksum did not match. Nothing was installed; reopen this file to retry.' };
      Expand-Archive -LiteralPath $Archive -DestinationPath $Temporary;
      $Binary = Get-ChildItem -LiteralPath $Temporary -Filter uv.exe -Recurse | Select-Object -First 1;
      if (-not $Binary) { throw 'Runtime archive did not contain uv.exe.' };
      $UvBin = Join-Path $Runtime 'uv.exe';
      Copy-Item -LiteralPath $Binary.FullName -Destination $UvBin;
    } finally { Remove-Item -LiteralPath $Temporary -Recurse -Force };
  };
  Write-Output 'Opening med-lit settings...';
  if ($Checkout) { & $UvBin run --directory $Checkout med-lit-mcp setup --ui --client chatgpt --dev $Checkout } else { & $UvBin tool run --python 3.11 --from 'med-lit-mcp==@VERSION@' med-lit-mcp setup --ui --client chatgpt --launcher $UvBin };
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE };
  Write-Output 'med-lit settings saved and connection verified. You can close this window.';
  exit 0;
} catch { Write-Error $_.Exception.Message; exit 1 }
