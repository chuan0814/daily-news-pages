$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ($MyInvocation.MyCommand.Path) {
  $BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
} else {
  $BaseDir = (Get-Location).Path
}
$Now = Get-Date
$Cutoff = $Now.AddHours(-6)
$TargetCount = 30
$MinimumCount = 20

$Feeds = @(
  @{ Source = "Yahoo新聞"; Url = "https://tw.news.yahoo.com/rss" },
  @{ Source = "TVBS新聞網"; Url = "https://news.tvbs.com.tw/web_api/play_feed_realtime" },
  @{ Source = "東森新聞網"; Url = "https://feeds.feedburner.com/ettoday/realtime" }
)

function Clean-Text([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
  $text = [regex]::Replace($Value, "<[^>]+>", "")
  $text = [System.Net.WebUtility]::HtmlDecode($text)
  return ([regex]::Replace($text, "\s+", " ")).Trim()
}

function Short-Summary([string]$Title, [string]$Description) {
  $text = Clean-Text $Description
  if (-not $text) { $text = Clean-Text $Title }
  $text = [regex]::Replace($text, "^[^：:]{1,12}[：:]", "").Trim()
  if ($text.Length -gt 50) { return $text.Substring(0, 50) }
  return $text
}

function Get-Category([string]$Title, [string]$Summary, [string]$Hint) {
  $text = "$Title $Summary $Hint"
  $rules = @(
    @{ Name = "電競遊戲"; Words = @("電競", "遊戲", "Steam", "Switch", "PS5", "Xbox", "英雄聯盟", "任天堂") },
    @{ Name = "AI科技"; Words = @("AI", "人工智慧", "輝達", "NVIDIA", "台積電", "半導體", "晶片", "科技", "機器人", "資料中心") },
    @{ Name = "財經/證券"; Words = @("台股", "股", "證券", "金管會", "匯率", "美元", "日圓", "財經", "經濟", "央行", "關稅", "基金", "投資") },
    @{ Name = "政治"; Words = @("總統", "行政院", "立院", "立法院", "民進黨", "國民黨", "民眾黨", "罷免", "選舉", "市長", "政治") },
    @{ Name = "國際"; Words = @("美國", "中國", "日本", "韓國", "歐盟", "以色列", "伊朗", "烏克蘭", "川普", "國際", "全球") }
  )
  foreach ($rule in $rules) {
    foreach ($word in $rule.Words) {
      if ($text.IndexOf($word, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $rule.Name }
    }
  }
  return "國內"
}

function Parse-DateTime([string]$Raw) {
  $clean = Clean-Text $Raw
  if (-not $clean) { return $null }
  try { return ([DateTimeOffset]::Parse($clean)).LocalDateTime } catch {}
  foreach ($fmt in @("yyyy-MM-dd HH:mm", "yyyy/MM/dd HH:mm", "MM/dd HH:mm")) {
    try {
      $dt = [datetime]::ParseExact($clean, $fmt, [Globalization.CultureInfo]::InvariantCulture)
      if ($fmt -eq "MM/dd HH:mm") { $dt = Get-Date -Year $Now.Year -Month $dt.Month -Day $dt.Day -Hour $dt.Hour -Minute $dt.Minute }
      return $dt
    } catch {}
  }
  return $null
}

function Fetch-RssItems($Feed) {
  [xml]$xml = (Invoke-WebRequest -Uri $Feed.Url -UseBasicParsing -TimeoutSec 30).Content
  $items = @()
  foreach ($node in $xml.rss.channel.item) {
    $title = Clean-Text $node.title.InnerText
    $link = if ($node.link.InnerText) { Clean-Text $node.link.InnerText } else { Clean-Text ([string]$node.link) }
    $description = $node.description.InnerText
    $pubDate = Parse-DateTime ([string]$node.pubDate)
    if (-not $title -or -not $link -or -not $pubDate) { continue }
    if ($pubDate -lt $Cutoff -or $pubDate -gt $Now.AddMinutes(10)) { continue }
    $summary = Short-Summary $title $description
    $category = Get-Category $title $summary ([string]$node.category)
    $items += [pscustomobject]@{
      Category = $category; Source = $Feed.Source; Time = $pubDate.ToString("MM/dd HH:mm")
      Title = $title; Summary = $summary; Url = $link; SortTime = $pubDate
    }
  }
  return $items
}

function Fetch-ChinatimesItems {
  $page = (Invoke-WebRequest -Uri "https://www.chinatimes.com/realtimenews/?chdtv" -UseBasicParsing -TimeoutSec 30).Content
  $pattern = '<h3 class="title"><a href="(?<link>[^"]+)">(?<title>.*?)</a></h3>\s*<div class="meta-info">\s*<time datetime="(?<time>[^"]+)".*?</time>\s*<div class="category"><a [^>]*>(?<category>.*?)</a></div>\s*</div>\s*<p class="intro">(?<summary>.*?)</p>'
  $items = @()
  foreach ($m in [regex]::Matches($page, $pattern, [Text.RegularExpressions.RegexOptions]::Singleline)) {
    $title = Clean-Text $m.Groups["title"].Value
    $summary = Short-Summary $title $m.Groups["summary"].Value
    $pubDate = Parse-DateTime $m.Groups["time"].Value
    if (-not $title -or -not $pubDate) { continue }
    if ($pubDate -lt $Cutoff -or $pubDate -gt $Now.AddMinutes(10)) { continue }
    $link = $m.Groups["link"].Value
    if ($link.StartsWith("/")) { $link = "https://www.chinatimes.com$link" }
    $hint = Clean-Text $m.Groups["category"].Value
    $items += [pscustomobject]@{
      Category = Get-Category $title $summary $hint; Source = "中時新聞網"; Time = $pubDate.ToString("MM/dd HH:mm")
      Title = $title; Summary = $summary; Url = $link; SortTime = $pubDate
    }
  }
  return $items
}

$all = @()
$errors = @()
foreach ($feed in $Feeds) {
  try { $all += Fetch-RssItems $feed } catch { $errors += "$($feed.Source): $($_.Exception.Message)" }
}
try { $all += Fetch-ChinatimesItems } catch { $errors += "中時新聞網: $($_.Exception.Message)" }

$seen = @{}
$preferred = @("政治", "國際", "財經/證券", "AI科技", "電競遊戲")
$unique = $all | Sort-Object SortTime -Descending | Where-Object {
  $key = [regex]::Replace($_.Title.ToLowerInvariant(), "\W+", "")
  if ($seen.ContainsKey($key)) { $false } else { $seen[$key] = $true; $true }
}
$focused = @($unique | Where-Object { $preferred -contains $_.Category })
$fallback = @($unique | Where-Object { $preferred -notcontains $_.Category })
$sourceOrder = @("Yahoo新聞", "中時新聞網", "TVBS新聞網", "東森新聞網")
$buckets = @{}
foreach ($source in $sourceOrder) {
  $sourceFocused = @($focused | Where-Object { $_.Source -eq $source })
  $sourceFallback = @($fallback | Where-Object { $_.Source -eq $source })
  $buckets[$source] = [System.Collections.ArrayList]@($sourceFocused + $sourceFallback)
}
$balanced = [System.Collections.ArrayList]@()
while ($balanced.Count -lt $TargetCount) {
  $added = $false
  foreach ($source in $sourceOrder) {
    if ($balanced.Count -ge $TargetCount) { break }
    if ($buckets[$source].Count -gt 0) {
      [void]$balanced.Add($buckets[$source][0])
      $buckets[$source].RemoveAt(0)
      $added = $true
    }
  }
  if (-not $added) { break }
}
$selected = [System.Collections.ArrayList]@()
$selectedKeys = @{}
foreach ($item in $balanced) {
  if ($selected.Count -ge $TargetCount) { break }
  $key = [regex]::Replace($item.Title.ToLowerInvariant(), "\W+", "")
  if (-not $selectedKeys.ContainsKey($key)) {
    [void]$selected.Add($item)
    $selectedKeys[$key] = $true
  }
}
foreach ($item in @($focused + $fallback)) {
  if ($selected.Count -ge $TargetCount) { break }
  $key = [regex]::Replace($item.Title.ToLowerInvariant(), "\W+", "")
  if (-not $selectedKeys.ContainsKey($key)) {
    [void]$selected.Add($item)
    $selectedKeys[$key] = $true
  }
}
$items = @($selected | Select-Object -First $TargetCount)
if ($items.Count -lt $MinimumCount) {
  throw "最近 6 小時只取得 $($items.Count) 則新聞；錯誤：$($errors -join '; ')"
}

$id = 0
$items = $items | ForEach-Object {
  $id += 1
  [pscustomobject]@{
    Id = $id; Category = $_.Category; Source = $_.Source; Time = $_.Time
    Title = $_.Title; Summary = $_.Summary; Url = $_.Url
  }
}

function Html-Encode([string]$Value) { return [System.Net.WebUtility]::HtmlEncode($Value) }
$groups = $items | Group-Object Category
$updatedAt = (Get-Date).ToString("yyyy/MM/dd HH:mm")
$tabs = '<button class="filter-chip is-active" type="button" data-filter="全部">全部</button>'
foreach ($g in $groups) { $tabs += '<button class="filter-chip" type="button" data-filter="' + (Html-Encode $g.Name) + '">' + (Html-Encode $g.Name) + ' <span>' + $g.Count + '</span></button>' }

$leadCards = ""
foreach ($item in ($items | Select-Object -First 4)) {
  $leadCards += @"
<article class="lead-card" data-category="$(Html-Encode $item.Category)" data-source="$(Html-Encode $item.Source)">
  <div class="lead-meta"><span class="badge">$($item.Id)</span><span class="badge badge-soft">$(Html-Encode $item.Category)</span><span class="badge badge-soft">$(Html-Encode $item.Source)</span></div>
  <h2>$(Html-Encode $item.Title)</h2><p>$(Html-Encode $item.Summary)</p>
  <div class="lead-footer"><span>$(Html-Encode $item.Time)</span><a href="$(Html-Encode $item.Url)" target="_blank" rel="noopener noreferrer">查看原文</a></div>
</article>
"@
}

$sections = ""
foreach ($g in $groups) {
  $cards = ""
  foreach ($item in $g.Group) {
    $cards += @"
<article class="news-item" data-category="$(Html-Encode $item.Category)" data-source="$(Html-Encode $item.Source)">
  <div class="item-top"><div class="item-badges"><span class="item-index">$("{0:D2}" -f $item.Id)</span><span class="item-source">$(Html-Encode $item.Source)</span></div><time>$(Html-Encode $item.Time)</time></div>
  <h3>$(Html-Encode $item.Title)</h3><p>$(Html-Encode $item.Summary)</p><a href="$(Html-Encode $item.Url)" target="_blank" rel="noopener noreferrer">開啟原文</a>
</article>
"@
  }
  $sections += '<section class="category-block" data-category-group="' + (Html-Encode $g.Name) + '"><div class="section-head"><h2>' + (Html-Encode $g.Name) + '</h2><span>' + $g.Count + ' 則</span></div><div class="news-grid">' + $cards + '</div></section>'
}

$rows = ""
foreach ($item in $items) {
  $rows += "<tr data-category=""$(Html-Encode $item.Category)""><td class=""cell-index"">$($item.Id)</td><td>$(Html-Encode $item.Category)</td><td>$(Html-Encode $item.Source)</td><td>$(Html-Encode $item.Time)</td><td class=""cell-title"">$(Html-Encode $item.Title)</td><td>$(Html-Encode $item.Summary)</td><td><a href=""$(Html-Encode $item.Url)"" target=""_blank"" rel=""noopener noreferrer"">原文</a></td></tr>"
}

$sourceChips = ""
foreach ($g in ($items | Group-Object Source)) { $sourceChips += '<span class="chip"><strong>' + (Html-Encode $g.Name) + '</strong><em>' + $g.Count + ' 則</em></span>' }
$categoryChips = ""
foreach ($g in $groups) { $categoryChips += '<span class="chip"><strong>' + (Html-Encode $g.Name) + '</strong><em>' + $g.Count + ' 則</em></span>' }

$html = @"
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>每日新聞核心話題 即時版</title>
  <style>
    :root { color-scheme: light; --bg:#eef3f8; --surface:#fff; --surface-2:#f7fafe; --line:#d7e0eb; --text:#122033; --muted:#617286; --accent:#0b57d0; --accent-2:#08306b; --accent-soft:#e9f1ff; --shadow:0 10px 28px rgba(15,35,55,.08); }
    * { box-sizing:border-box; } body { margin:0; font-family:"Microsoft JhengHei","Noto Sans TC",Arial,sans-serif; background:var(--bg); color:var(--text); line-height:1.5; } a { color:var(--accent); text-decoration:none; font-weight:700; } a:hover { text-decoration:underline; }
    .page { width:min(1360px,calc(100% - 28px)); margin:18px auto 34px; } .hero { background:linear-gradient(135deg,#0a2342,#154c79 68%,#1c7293); color:#fff; border-radius:8px; padding:28px; box-shadow:var(--shadow); }
    .hero-top { display:flex; justify-content:space-between; gap:20px; align-items:flex-start; margin-bottom:20px; } .hero h1 { margin:0 0 10px; font-size:32px; line-height:1.2; } .hero p { margin:6px 0; color:rgba(255,255,255,.9); font-size:14px; }
    .hero-side { min-width:240px; background:rgba(255,255,255,.1); border:1px solid rgba(255,255,255,.12); border-radius:8px; padding:14px 16px; } .hero-side strong { display:block; font-size:12px; opacity:.8; margin-bottom:6px; } .hero-side span { display:block; font-size:20px; font-weight:700; margin-bottom:10px; }
    .toolbar { display:grid; grid-template-columns:minmax(0,1.2fr) minmax(0,1fr); gap:14px; margin-top:18px; } .toolbar-card,.stat-card,.lead-card,.category-block,.table-panel { background:var(--surface); border:1px solid var(--line); border-radius:8px; box-shadow:var(--shadow); }
    .toolbar-card { padding:14px 16px; } .toolbar-label,.stat-label { color:var(--muted); font-size:13px; margin-bottom:10px; } .search-input { width:100%; height:42px; border-radius:8px; border:1px solid var(--line); padding:0 14px; font-size:15px; }
    .filter-row,.chips,.lead-meta,.item-badges { display:flex; flex-wrap:wrap; gap:8px; align-items:center; } .filter-chip { border:1px solid var(--line); background:#fff; color:var(--text); border-radius:999px; padding:8px 12px; font-size:13px; cursor:pointer; } .filter-chip.is-active { background:var(--accent); border-color:var(--accent); color:#fff; }
    .stats-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:18px 0; } .stat-card { padding:16px; } .stat-card-wide { grid-column:span 2; } .stat-value { font-size:32px; font-weight:800; color:var(--accent-2); } .stat-value-small { font-size:22px; }
    .chip,.badge,.item-index { display:inline-flex; align-items:center; justify-content:center; border-radius:999px; } .chip { gap:8px; padding:7px 11px; background:var(--accent-soft); color:var(--accent-2); font-size:13px; } .chip em { color:var(--muted); font-style:normal; }
    .lead-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:18px; } .lead-card { padding:16px; min-height:220px; display:flex; flex-direction:column; } .badge { min-width:28px; height:28px; padding:0 10px; background:var(--accent); color:#fff; font-size:12px; font-weight:700; } .badge-soft { background:var(--accent-soft); color:var(--accent-2); }
    .lead-card h2 { margin:14px 0 10px; font-size:20px; line-height:1.35; } .lead-card p,.news-item p { margin:0; color:#36495f; font-size:14px; } .lead-footer { display:flex; justify-content:space-between; gap:10px; margin-top:auto; padding-top:14px; color:var(--muted); font-size:13px; }
    .category-stack { display:grid; gap:16px; } .category-block { padding:16px; } .section-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:14px; } .section-head h2 { margin:0; font-size:22px; } .section-head span { color:var(--muted); font-size:13px; }
    .news-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; } .news-item { background:var(--surface-2); border:1px solid var(--line); border-radius:8px; padding:14px; } .item-top { display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:10px; } .item-index { min-width:34px; height:28px; background:#dce9ff; color:var(--accent-2); font-size:12px; font-weight:800; } .item-source,.item-top time { font-size:12px; color:var(--muted); font-weight:700; } .news-item h3 { margin:0 0 8px; font-size:18px; line-height:1.35; } .news-item p { margin-bottom:10px; }
    .table-panel { margin-top:18px; overflow:hidden; } .panel-head { padding:14px 16px; display:flex; justify-content:space-between; gap:10px; border-bottom:1px solid var(--line); } .panel-head h2 { margin:0; font-size:20px; } .panel-head span { color:var(--muted); font-size:13px; } .table-wrap { overflow-x:auto; } table { width:100%; min-width:1050px; border-collapse:collapse; } th,td { padding:12px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:14px; } th { position:sticky; top:0; background:#edf4ff; color:#15385a; z-index:1; } .cell-index { width:60px; text-align:center; font-weight:800; color:var(--accent); } .cell-title { min-width:320px; font-weight:700; }
    .empty-state { display:none; background:#fff7f6; border:1px solid #f3d2cf; color:#7a2620; border-radius:8px; padding:14px 16px; margin:18px 0 0; } .footer-note { color:var(--muted); font-size:13px; margin-top:14px; text-align:center; } .hidden { display:none!important; }
    @media (max-width:1180px) { .lead-grid,.news-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } .stats-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media (max-width:860px) { .page { width:min(100% - 18px,100%); margin:10px auto 24px; } .hero { padding:18px; } .hero-top,.toolbar { grid-template-columns:1fr; display:grid; } .hero h1 { font-size:26px; } .lead-grid,.news-grid,.stats-grid { grid-template-columns:1fr; } .stat-card-wide { grid-column:auto; } .panel-head { align-items:flex-start; flex-direction:column; } }
  </style>
</head>
<body>
  <main class="page">
    <section class="hero"><div class="hero-top"><div><h1>每日新聞核心話題 即時版</h1><p>聚焦最近 6 小時內的重要新聞，來源限定 Yahoo新聞、中時新聞網、TVBS新聞網、東森新聞網。</p><p>主題涵蓋政治、財經、證券、AI科技、電競遊戲，方便你直接在瀏覽器閱讀。</p></div><aside class="hero-side"><strong>最後更新</strong><span id="updatedAt">$updatedAt</span><small>排程時間：每日 01:00 / 07:00 / 13:00 / 19:00</small></aside></div><div class="toolbar"><div class="toolbar-card"><div class="toolbar-label">搜尋標題、摘要或來源</div><input id="searchInput" class="search-input" type="search" placeholder="例如：台積電、AI、以色列、遊戲股"></div><div class="toolbar-card"><div class="toolbar-label">依類別篩選</div><div class="filter-row" id="filterRow">$tabs</div></div></div></section>
    <section class="stats-grid"><div class="stat-card"><div class="stat-label">本輪新聞數</div><div class="stat-value">$($items.Count)</div></div><div class="stat-card"><div class="stat-label">更新頻率</div><div class="stat-value stat-value-small">每 6 小時</div></div><div class="stat-card stat-card-wide"><div class="stat-label">來源分布</div><div class="chips">$sourceChips</div></div><div class="stat-card stat-card-wide"><div class="stat-label">類別分布</div><div class="chips">$categoryChips</div></div></section>
    <section class="lead-grid" id="leadGrid">$leadCards</section>
    <div class="empty-state" id="emptyState">目前沒有符合搜尋或篩選條件的新聞。</div>
    <section class="category-stack" id="categoryStack">$sections</section>
    <section class="table-panel"><div class="panel-head"><h2>完整清單</h2><span>想快速掃過全部標題時，可以看這個表格。</span></div><div class="table-wrap"><table><thead><tr><th>#</th><th>類別</th><th>來源</th><th>時間</th><th>標題</th><th>摘要</th><th>原文</th></tr></thead><tbody id="newsTableBody">$rows</tbody></table></div></section>
    <p class="footer-note">這是本機 HTML 檔，直接雙擊就能在瀏覽器開啟；之後每次排程都會覆蓋同一份檔案。</p>
  </main>
  <script>
    const state = { keyword: "", category: "全部" };
    const filterRow = document.getElementById("filterRow");
    const searchInput = document.getElementById("searchInput");
    const tableRows = Array.from(document.querySelectorAll("#newsTableBody tr"));
    const cards = Array.from(document.querySelectorAll(".news-item"));
    const leadCards = Array.from(document.querySelectorAll(".lead-card"));
    const groups = Array.from(document.querySelectorAll("[data-category-group]"));
    const emptyState = document.getElementById("emptyState");
    function textMatch(node, keyword) { return !keyword || node.textContent.toLowerCase().includes(keyword); }
    function categoryMatch(category) { return state.category === "全部" || category === state.category; }
    function applyFilters() {
      const keyword = state.keyword.trim().toLowerCase(); let visibleCount = 0;
      cards.forEach((card) => { const ok = categoryMatch(card.dataset.category) && textMatch(card, keyword); card.classList.toggle("hidden", !ok); if (ok) visibleCount += 1; });
      groups.forEach((group) => { const hasVisible = Array.from(group.querySelectorAll(".news-item")).some((card) => !card.classList.contains("hidden")); group.classList.toggle("hidden", !categoryMatch(group.dataset.categoryGroup) || !hasVisible); });
      tableRows.forEach((row) => { row.classList.toggle("hidden", !(categoryMatch(row.dataset.category) && textMatch(row, keyword))); });
      leadCards.forEach((card) => { card.classList.toggle("hidden", !(categoryMatch(card.dataset.category) && textMatch(card, keyword))); });
      emptyState.style.display = visibleCount === 0 ? "block" : "none";
    }
    filterRow.addEventListener("click", (event) => { const button = event.target.closest(".filter-chip"); if (!button) return; state.category = button.dataset.filter; document.querySelectorAll(".filter-chip").forEach((chip) => chip.classList.remove("is-active")); button.classList.add("is-active"); applyFilters(); });
    searchInput.addEventListener("input", () => { state.keyword = searchInput.value; applyFilters(); });
  </script>
</body>
</html>
"@

$outputs = @("index.html", "每日新聞核心話題.html", "每日新聞核心話題_近24小時_20260623.html")
foreach ($name in $outputs) {
  [System.IO.File]::WriteAllText((Join-Path $BaseDir $name), $html, [System.Text.UTF8Encoding]::new($false))
}
Write-Output "HTML 已輸出：$($items.Count) 則新聞，最後更新 $updatedAt"
