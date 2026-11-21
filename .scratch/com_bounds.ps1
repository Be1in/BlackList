$ErrorActionPreference="Stop"
$app = New-Object -ComObject PowerPoint.Application
$pres = $app.Presentations.Open("d:\BlackList\.scratch\template.pptx", $true, $false, $false)
$sl = $pres.Slides.Item(6)
$sh = $null
foreach ($s in $sl.Shapes) { if ($s.Type -eq 6) { $sh = $s } }
"group L,T,W,H: {0:N2} {1:N2} {2:N2} {3:N2}" -f $sh.Left,$sh.Top,$sh.Width,$sh.Height
$minx=1e9;$miny=1e9;$maxx=-1e9;$maxy=-1e9
foreach ($c in $sh.GroupItems) {
  "child {0}: {1:N2} {2:N2} {3:N2} {4:N2} type {5}" -f $c.Name,$c.Left,$c.Top,$c.Width,$c.Height,$c.Type
  if ($c.Left -lt $minx) {$minx=$c.Left}
  if ($c.Top -lt $miny) {$miny=$c.Top}
  if (($c.Left+$c.Width) -gt $maxx) {$maxx=$c.Left+$c.Width}
  if (($c.Top+$c.Height) -gt $maxy) {$maxy=$c.Top+$c.Height}
}
"child union: {0:N2} {1:N2} {2:N2} {3:N2}" -f $minx,$miny,$maxx,$maxy
"overflow right {0:N2} bottom {1:N2}" -f ($maxx-($sh.Left+$sh.Width)),($maxy-($sh.Top+$sh.Height))
$pres.Close(); $app.Quit()
