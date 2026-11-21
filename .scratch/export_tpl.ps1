$ErrorActionPreference = "Stop"
$src = "d:\BlackList\.scratch\template.pptx"
$out = "d:\BlackList\.scratch\tpl_png"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$app = New-Object -ComObject PowerPoint.Application
$pres = $app.Presentations.Open($src, $true, $false, $false)
$pres.Export($out, "PNG", 1280, 720)
$pres.Close()
$app.Quit()
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null
Get-ChildItem $out | Select-Object Name,Length
