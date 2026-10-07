# Report

`report.pdf` is the submission (A4, 6 pages). It is printed from `report.html`, which is generated
from `report.tpl.html` + `build_charts.py` (the charts are drawn from the numbers in
`backend/eval/results/`).

Fill in the highlighted placeholders (team names, video link, work division, AI-use additions) in
`report.tpl.html`, then rebuild:

```powershell
backend\.venv\Scripts\python docs\report\build_charts.py docs\report\report.tpl.html docs\report\report.html
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new --no-pdf-header-footer `
  --print-to-pdf="$PWD\docs\report\report.pdf" "file:///$($PWD -replace '\','/')/docs/report/report.html"
```
