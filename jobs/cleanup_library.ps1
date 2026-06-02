# cleanup_library.ps1
# Deletes duplicate files (_1, _2, _1_1, etc.) from the KB library.
# Keeps the original (no numeric suffix). Logs everything before deleting.

$library = "F:\Knowledge_Database\_library"
$logFile = "D:\OneDrive\Claude\agents\watson\logs\library_cleanup.log"

# Regex to match files ending in _N or _N_N etc. before the extension
$dupPattern = '^(.+?)(_\d+)+(\..+)$'

$files = Get-ChildItem -Path $library -File
$toDelete = @()
$skipped = @()

foreach ($file in $files) {
    if ($file.Name -match $dupPattern) {
        $toDelete += $file
    } else {
        $skipped += $file
    }
}

# Log what we're about to do
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$timestamp] Library cleanup started" | Out-File $logFile -Append
"Keeping $($skipped.Count) original files." | Out-File $logFile -Append
"Deleting $($toDelete.Count) duplicate files." | Out-File $logFile -Append
"" | Out-File $logFile -Append

foreach ($file in $toDelete) {
    "  DELETE: $($file.Name)" | Out-File $logFile -Append
}

# Confirm before deleting
Write-Host ""
Write-Host "========================================"
Write-Host "  Library Cleanup"
Write-Host "========================================"
Write-Host "  Keeping:  $($skipped.Count) original files"
Write-Host "  Deleting: $($toDelete.Count) duplicate files"
Write-Host ""
Write-Host "  Log: $logFile"
Write-Host ""
$confirm = Read-Host "  Type YES to proceed with deletion"

if ($confirm -eq "YES") {
    $deleted = 0
    $errors = 0
    foreach ($file in $toDelete) {
        try {
            Remove-Item $file.FullName -Force
            $deleted++
        } catch {
            Write-Host "  ERROR deleting $($file.Name): $_"
            "  ERROR: $($file.Name) - $_" | Out-File $logFile -Append
            $errors++
        }
    }
    Write-Host ""
    Write-Host "  Done. Deleted $deleted files. Errors: $errors"
    "[$timestamp] Done. Deleted $deleted files. Errors: $errors" | Out-File $logFile -Append
} else {
    Write-Host "  Cancelled. Nothing deleted."
    "[$timestamp] Cancelled by user." | Out-File $logFile -Append
}
