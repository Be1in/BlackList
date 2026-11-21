$ErrorActionPreference = "Stop"
$src = "d:\BlackList\MSBD5017_Project_Pitch.pptx"
$out = "d:\BlackList\.scratch\render1"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$app = New-Object -ComObject PowerPoint.Application
$pres = $app.Presentations.Open($src, $true, $false, $false)
$pres.Export($out, "PNG", 1600, 900)
$pres.Close()
$app.Quit()
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null
Get-ChildItem $out | Select-Object Name,Length
