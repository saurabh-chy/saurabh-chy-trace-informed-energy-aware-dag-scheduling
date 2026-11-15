param(
    [ValidateSet("1","2","all")]
    [string]$Stage = "all",

    [string]$BasePath = "D:\New folder",

    [int]$MaximumJobs = 500,

    [long]$MaximumPlannedInstances = 250000,

    [int]$MachineUsageMachineLimit = 200
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor DarkCyan
    Write-Host $Message -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor DarkCyan
}

function Find-FirstFile {
    param(
        [string[]]$PreferredPaths,
        [string]$ExactName
    )

    foreach ($path in $PreferredPaths) {
        if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            return (Get-Item -LiteralPath $path).FullName
        }
    }

    $found = Get-ChildItem -LiteralPath $BasePath -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -ieq $ExactName -and
            $_.FullName -notlike "*\Alibaba2018_Real_Subset\*"
        } |
        Select-Object -First 1

    if ($found) {
        return $found.FullName
    }

    return $null
}

function Find-Archive {
    param([string]$Stem)

    $pattern = "^" + [regex]::Escape($Stem) + "\.tar(\.gz)?$"

    $found = Get-ChildItem -LiteralPath $BasePath -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match $pattern -and
            $_.FullName -notlike "*\Alibaba2018_Real_Subset\*"
        } |
        Sort-Object Length -Descending |
        Select-Object -First 1

    if ($found) {
        return $found.FullName
    }

    return $null
}

function Get-FirstTarMember {
    param(
        [string]$TarExecutable,
        [string]$ArchivePath,
        [string]$ExpectedLeafName
    )

    # Alibaba's archives normally contain one CSV. Reading only the first
    # archive header avoids a full extraction.
    $member = & $TarExecutable -tf $ArchivePath 2>$null |
        Where-Object { $_ -and $_.Trim().Length -gt 0 } |
        Select-Object -First 1

    if (-not $member) {
        throw "Could not read an archive member from: $ArchivePath"
    }

    $member = $member.Trim()
    $leaf = ($member -split "[\\/]")[-1]

    if ($leaf -ine $ExpectedLeafName) {
        Write-Warning "The first member is '$member', not '$ExpectedLeafName'. It will still be used."
    }

    return $member
}

if (-not (Test-Path -LiteralPath $BasePath -PathType Container)) {
    throw "The base folder does not exist: $BasePath"
}

$OutputDirectory = Join-Path $BasePath "Alibaba2018_Real_Subset"
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$TaskCsv = Find-FirstFile `
    -PreferredPaths @(
        (Join-Path $BasePath "batch_task\batch_task.csv"),
        (Join-Path $BasePath "batch_task.csv")
    ) `
    -ExactName "batch_task.csv"

$MachineMetaCsv = Find-FirstFile `
    -PreferredPaths @(
        (Join-Path $BasePath "machine_meta\machine_meta.csv"),
        (Join-Path $BasePath "machine_meta.csv")
    ) `
    -ExactName "machine_meta.csv"

$BatchInstanceCsv = Find-FirstFile -PreferredPaths @(
    (Join-Path $BasePath "batch_instance\batch_instance.csv"),
    (Join-Path $BasePath "batch_instance.csv")
) -ExactName "batch_instance.csv"

$MachineUsageCsv = Find-FirstFile -PreferredPaths @(
    (Join-Path $BasePath "machine_usage\machine_usage.csv"),
    (Join-Path $BasePath "machine_usage.csv")
) -ExactName "machine_usage.csv"

$BatchInstanceArchive = Find-Archive "batch_instance"
$MachineUsageArchive = Find-Archive "machine_usage"

$TarExecutable = $null
try {
    $TarExecutable = (Get-Command tar.exe -ErrorAction Stop).Source
} catch {
    try {
        $TarExecutable = (Get-Command tar -ErrorAction Stop).Source
    } catch {
        $TarExecutable = $null
    }
}

if (-not $TaskCsv) {
    throw "batch_task.csv was not found under '$BasePath'."
}
if (-not $MachineMetaCsv) {
    throw "machine_meta.csv was not found under '$BasePath'."
}

Write-Host ""
Write-Host "Alibaba Cluster Trace v2018 real-subset builder" -ForegroundColor Green
Write-Host "Base folder:            $BasePath"
Write-Host "Output folder:          $OutputDirectory"
Write-Host "batch_task.csv:         $TaskCsv"
Write-Host "machine_meta.csv:       $MachineMetaCsv"

if ($BatchInstanceCsv) {
    Write-Host "batch_instance source:  $BatchInstanceCsv"
} elseif ($BatchInstanceArchive) {
    Write-Host "batch_instance source:  $BatchInstanceArchive"
} else {
    Write-Host "batch_instance source:  NOT FOUND" -ForegroundColor Red
}

if ($MachineUsageCsv) {
    Write-Host "machine_usage source:   $MachineUsageCsv"
} elseif ($MachineUsageArchive) {
    Write-Host "machine_usage source:   $MachineUsageArchive"
} else {
    Write-Host "machine_usage source:   NOT FOUND" -ForegroundColor Red
}

$csharp = @'
using System;
using System.IO;
using System.Text;
using System.Linq;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Text.RegularExpressions;

public class AlibabaFilterStats
{
    public long LinesRead { get; set; }
    public long MatchedRows { get; set; }
    public long ValidRows { get; set; }
    public long InvalidRows { get; set; }
    public long MalformedRows { get; set; }
    public int DistinctMachines { get; set; }
}

public class AlibabaTaskStats
{
    public int JobsObserved { get; set; }
    public int EligibleJobs { get; set; }
    public int SelectedJobs { get; set; }
    public long SelectedPlannedInstances { get; set; }
    public long SelectedTasks { get; set; }
    public long DagEdges { get; set; }
}

public class AlibabaMachineStats
{
    public int MachinesReferencedByValidInstances { get; set; }
    public int MachinesFoundInMetadata { get; set; }
    public int MachinesSelectedForUsage { get; set; }
    public long MetadataRowsForReferencedMachines { get; set; }
    public long MetadataRowsForSelectedMachines { get; set; }
}

internal class AlibabaJobStat
{
    public string JobName;
    public long TaskCount;
    public long PlannedInstances;
    public long SuccessfulTaskCount;
    public bool AllStructurallyValid = true;
    public bool HasDependency;
    public long StartTime = long.MaxValue;
    public long EndTime = long.MinValue;
    public string Tier = "";
    public string Category = "";
}

internal class AlibabaTaskEdgeCandidate
{
    public string JobName;
    public string ChildTaskName;
    public int ChildNumber;
    public List<int> ParentNumbers = new List<int>();
}

internal class AlibabaMetaRow
{
    public string[] Fields;
    public long TimeStamp;
}

internal class AlibabaUsageAggregate
{
    public long Rows;
    public long ValidRows;
    public double CpuSum;
    public double MemSum;
    public double NetInSum;
    public double NetOutSum;
    public double DiskSum;
}

public static class AlibabaSubsetBuilderV1
{
    private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
    private static readonly Regex DagPattern = new Regex(
        @"^[A-Za-z](\d+)((?:_\d+)*)$",
        RegexOptions.Compiled
    );

    private static StreamWriter NewWriter(string path)
    {
        return new StreamWriter(path, false, new UTF8Encoding(false), 1024 * 1024);
    }

    private static bool ParseLongFlexible(string value, out long result)
    {
        value = (value ?? "").Trim();
        if (long.TryParse(value, NumberStyles.Integer, Inv, out result))
            return true;

        double d;
        if (double.TryParse(value, NumberStyles.Float, Inv, out d) &&
            !double.IsNaN(d) && !double.IsInfinity(d))
        {
            result = (long)d;
            return true;
        }

        result = 0;
        return false;
    }

    private static bool ParseDouble(string value, out double result)
    {
        value = (value ?? "").Trim();
        return double.TryParse(value, NumberStyles.Float, Inv, out result) &&
               !double.IsNaN(result) &&
               !double.IsInfinity(result);
    }

    private static bool IsSuccessfulStatus(string status)
    {
        string s = (status ?? "").Trim().ToLowerInvariant();
        return s == "terminated" ||
               s == "finished" ||
               s == "succeeded" ||
               s == "success" ||
               s == "completed";
    }

    private static string Csv(string value)
    {
        if (value == null) return "";
        if (value.IndexOfAny(new char[] { ',', '"', '\r', '\n' }) >= 0)
            return "\"" + value.Replace("\"", "\"\"") + "\"";
        return value;
    }

    private static string GetFieldFast(string line, int zeroBasedFieldIndex)
    {
        if (line == null) return null;

        int start = 0;
        int field = 0;

        for (int i = 0; i <= line.Length; i++)
        {
            if (i == line.Length || line[i] == ',')
            {
                if (field == zeroBasedFieldIndex)
                    return line.Substring(start, i - start).Trim();

                field++;
                start = i + 1;
            }
        }

        return null;
    }

    private static bool IsHeader(string[] fields, string firstField)
    {
        return fields != null &&
               fields.Length > 0 &&
               string.Equals(fields[0].Trim(), firstField, StringComparison.OrdinalIgnoreCase);
    }

    private static void ParseDagName(
        string taskName,
        out int taskNumber,
        out List<int> parents,
        out bool isDagEncoded)
    {
        taskNumber = -1;
        parents = new List<int>();
        isDagEncoded = false;

        Match match = DagPattern.Match((taskName ?? "").Trim());
        if (!match.Success)
            return;

        int parsedTask;
        if (!int.TryParse(match.Groups[1].Value, NumberStyles.Integer, Inv, out parsedTask))
            return;

        taskNumber = parsedTask;
        isDagEncoded = true;

        string parentPart = match.Groups[2].Value;
        if (!string.IsNullOrEmpty(parentPart))
        {
            string[] tokens = parentPart.TrimStart('_').Split('_');
            foreach (string token in tokens)
            {
                int p;
                if (int.TryParse(token, NumberStyles.Integer, Inv, out p))
                    parents.Add(p);
            }
        }
    }

    private static List<AlibabaJobStat> EvenlySpaced(List<AlibabaJobStat> input, int count)
    {
        List<AlibabaJobStat> output = new List<AlibabaJobStat>();
        if (input == null || input.Count == 0 || count <= 0)
            return output;

        if (count >= input.Count)
        {
            output.AddRange(input);
            return output;
        }

        HashSet<int> used = new HashSet<int>();
        if (count == 1)
        {
            output.Add(input[input.Count / 2]);
            return output;
        }

        for (int i = 0; i < count; i++)
        {
            int index = (int)Math.Round(
                i * (input.Count - 1.0) / (count - 1.0),
                MidpointRounding.AwayFromZero
            );

            if (used.Add(index))
                output.Add(input[index]);
        }

        return output;
    }

    private static void AddCandidatesWithinBudget(
        List<AlibabaJobStat> candidates,
        int targetCount,
        long budget,
        long globalLimit,
        List<AlibabaJobStat> selected,
        HashSet<string> selectedIds,
        ref long globalInstances)
    {
        long localInstances = 0;
        foreach (AlibabaJobStat job in EvenlySpaced(candidates, targetCount))
        {
            if (selectedIds.Contains(job.JobName))
                continue;

            if (localInstances + job.PlannedInstances > budget)
                continue;

            if (globalInstances + job.PlannedInstances > globalLimit)
                continue;

            selected.Add(job);
            selectedIds.Add(job.JobName);
            localInstances += job.PlannedInstances;
            globalInstances += job.PlannedInstances;
        }
    }

    public static AlibabaTaskStats PrepareTasks(
        string taskCsv,
        string selectedJobsPath,
        string jobsOutputPath,
        string tasksOutputPath,
        string edgesOutputPath,
        int maximumJobs,
        long maximumPlannedInstances)
    {
        Dictionary<string, AlibabaJobStat> jobs =
            new Dictionary<string, AlibabaJobStat>(StringComparer.Ordinal);

        using (StreamReader reader = new StreamReader(
            taskCsv, Encoding.UTF8, true, 1024 * 1024))
        {
            string line;
            long lineNumber = 0;

            while ((line = reader.ReadLine()) != null)
            {
                lineNumber++;
                if (line.Length == 0) continue;

                string[] f = line.Split(',');
                if (IsHeader(f, "task_name")) continue;
                if (f.Length < 9) continue;

                string taskName = f[0].Trim();
                string jobName = f[2].Trim();
                if (jobName.Length == 0) continue;

                AlibabaJobStat job;
                if (!jobs.TryGetValue(jobName, out job))
                {
                    job = new AlibabaJobStat();
                    job.JobName = jobName;
                    jobs.Add(jobName, job);
                }

                job.TaskCount++;

                long instanceNum, startTime, endTime;
                double planCpu, planMem;

                bool instanceOk = ParseLongFlexible(f[1], out instanceNum) && instanceNum > 0;
                bool startOk = ParseLongFlexible(f[5], out startTime) && startTime >= 0;
                bool endOk = ParseLongFlexible(f[6], out endTime) && endTime > startTime;
                bool cpuOk = ParseDouble(f[7], out planCpu) && planCpu > 0;
                bool memOk = ParseDouble(f[8], out planMem) && planMem >= 0 && planMem <= 100;

                if (instanceOk)
                {
                    if (long.MaxValue - job.PlannedInstances > instanceNum)
                        job.PlannedInstances += instanceNum;
                    else
                        job.AllStructurallyValid = false;
                }

                if (startOk && startTime < job.StartTime)
                    job.StartTime = startTime;

                if (endOk && endTime > job.EndTime)
                    job.EndTime = endTime;

                if (!(instanceOk && startOk && endOk && cpuOk && memOk))
                    job.AllStructurallyValid = false;

                if (IsSuccessfulStatus(f[4]))
                    job.SuccessfulTaskCount++;

                int ownNumber;
                List<int> parents;
                bool encoded;
                ParseDagName(taskName, out ownNumber, out parents, out encoded);
                if (encoded && parents.Count > 0)
                    job.HasDependency = true;

                if (lineNumber % 1000000 == 0)
                    Console.WriteLine("Scanned {0:N0} batch-task rows...", lineNumber);
            }
        }

        long perJobCap = Math.Max(1000, maximumPlannedInstances / 5);

        List<AlibabaJobStat> strict = jobs.Values
            .Where(j =>
                j.TaskCount >= 2 &&
                j.HasDependency &&
                j.AllStructurallyValid &&
                j.SuccessfulTaskCount == j.TaskCount &&
                j.PlannedInstances > 0 &&
                j.PlannedInstances <= perJobCap)
            .OrderBy(j => j.PlannedInstances)
            .ToList();

        List<AlibabaJobStat> relaxed = jobs.Values
            .Where(j =>
                j.TaskCount >= 2 &&
                j.HasDependency &&
                j.AllStructurallyValid &&
                j.PlannedInstances > 0 &&
                j.PlannedInstances <= perJobCap &&
                (j.TaskCount == 0 ? 0.0 : (double)j.SuccessfulTaskCount / j.TaskCount) >= 0.80)
            .OrderBy(j => j.PlannedInstances)
            .ToList();

        List<AlibabaJobStat> eligible = new List<AlibabaJobStat>();
        HashSet<string> eligibleIds = new HashSet<string>(StringComparer.Ordinal);

        foreach (AlibabaJobStat j in strict)
        {
            j.Tier = "strict_all_tasks_successful";
            if (eligibleIds.Add(j.JobName)) eligible.Add(j);
        }

        if (eligible.Count < maximumJobs)
        {
            foreach (AlibabaJobStat j in relaxed)
            {
                if (eligibleIds.Add(j.JobName))
                {
                    j.Tier = "relaxed_at_least_80_percent_successful";
                    eligible.Add(j);
                }
            }
        }

        eligible = eligible.OrderBy(j => j.PlannedInstances).ToList();

        int n = eligible.Count;
        int firstCut = n / 3;
        int secondCut = (2 * n) / 3;

        List<AlibabaJobStat> small = eligible.Take(firstCut).ToList();
        List<AlibabaJobStat> medium = eligible.Skip(firstCut).Take(secondCut - firstCut).ToList();
        List<AlibabaJobStat> large = eligible.Skip(secondCut).ToList();

        foreach (AlibabaJobStat j in small) j.Category = "small";
        foreach (AlibabaJobStat j in medium) j.Category = "medium";
        foreach (AlibabaJobStat j in large) j.Category = "large";

        int smallTarget = (int)Math.Round(maximumJobs * 0.50);
        int mediumTarget = (int)Math.Round(maximumJobs * 0.35);
        int largeTarget = Math.Max(0, maximumJobs - smallTarget - mediumTarget);

        long smallBudget = (long)Math.Round(maximumPlannedInstances * 0.25);
        long mediumBudget = (long)Math.Round(maximumPlannedInstances * 0.35);
        long largeBudget = maximumPlannedInstances - smallBudget - mediumBudget;

        List<AlibabaJobStat> selected = new List<AlibabaJobStat>();
        HashSet<string> selectedIds = new HashSet<string>(StringComparer.Ordinal);
        long selectedInstances = 0;

        AddCandidatesWithinBudget(
            small, smallTarget, smallBudget, maximumPlannedInstances,
            selected, selectedIds, ref selectedInstances);

        AddCandidatesWithinBudget(
            medium, mediumTarget, mediumBudget, maximumPlannedInstances,
            selected, selectedIds, ref selectedInstances);

        AddCandidatesWithinBudget(
            large, largeTarget, largeBudget, maximumPlannedInstances,
            selected, selectedIds, ref selectedInstances);

        // Fill any remaining job slots while respecting the global instance cap.
        foreach (AlibabaJobStat j in eligible)
        {
            if (selected.Count >= maximumJobs) break;
            if (selectedIds.Contains(j.JobName)) continue;
            if (selectedInstances + j.PlannedInstances > maximumPlannedInstances) continue;

            selected.Add(j);
            selectedIds.Add(j.JobName);
            selectedInstances += j.PlannedInstances;
        }

        selected = selected
            .OrderBy(j => j.Category)
            .ThenBy(j => j.PlannedInstances)
            .ThenBy(j => j.JobName)
            .ToList();

        using (StreamWriter writer = NewWriter(selectedJobsPath))
        {
            foreach (AlibabaJobStat j in selected)
                writer.WriteLine(j.JobName);
        }

        using (StreamWriter writer = NewWriter(jobsOutputPath))
        {
            writer.WriteLine(
                "job_name,category,task_count,planned_instance_count," +
                "successful_task_count,success_ratio,start_time,end_time," +
                "duration_seconds,has_dag_dependency,selection_tier");

            foreach (AlibabaJobStat j in selected)
            {
                double ratio = j.TaskCount == 0
                    ? 0.0
                    : (double)j.SuccessfulTaskCount / j.TaskCount;

                long duration =
                    j.StartTime == long.MaxValue || j.EndTime == long.MinValue
                    ? -1
                    : j.EndTime - j.StartTime;

                writer.WriteLine(
                    Csv(j.JobName) + "," +
                    Csv(j.Category) + "," +
                    j.TaskCount.ToString(Inv) + "," +
                    j.PlannedInstances.ToString(Inv) + "," +
                    j.SuccessfulTaskCount.ToString(Inv) + "," +
                    ratio.ToString("R", Inv) + "," +
                    (j.StartTime == long.MaxValue ? "" : j.StartTime.ToString(Inv)) + "," +
                    (j.EndTime == long.MinValue ? "" : j.EndTime.ToString(Inv)) + "," +
                    duration.ToString(Inv) + "," +
                    (j.HasDependency ? "true" : "false") + "," +
                    Csv(j.Tier)
                );
            }
        }

        Dictionary<string, Dictionary<int, string>> taskNumberMap =
            new Dictionary<string, Dictionary<int, string>>(StringComparer.Ordinal);

        List<AlibabaTaskEdgeCandidate> edgeCandidates =
            new List<AlibabaTaskEdgeCandidate>();

        long selectedTaskRows = 0;

        using (StreamReader reader = new StreamReader(
            taskCsv, Encoding.UTF8, true, 1024 * 1024))
        using (StreamWriter writer = NewWriter(tasksOutputPath))
        {
            writer.WriteLine(
                "task_name,instance_num,job_name,task_type,status,start_time," +
                "end_time,plan_cpu,plan_mem,task_duration_seconds," +
                "dag_task_number,parent_count");

            string line;
            while ((line = reader.ReadLine()) != null)
            {
                if (line.Length == 0) continue;

                string[] f = line.Split(',');
                if (IsHeader(f, "task_name")) continue;
                if (f.Length < 9) continue;

                string jobName = f[2].Trim();
                if (!selectedIds.Contains(jobName)) continue;

                long startTime, endTime;
                bool startOk = ParseLongFlexible(f[5], out startTime);
                bool endOk = ParseLongFlexible(f[6], out endTime);
                long duration = startOk && endOk ? endTime - startTime : -1;

                int ownNumber;
                List<int> parents;
                bool encoded;
                ParseDagName(f[0].Trim(), out ownNumber, out parents, out encoded);

                writer.WriteLine(
                    Csv(f[0].Trim()) + "," +
                    Csv(f[1].Trim()) + "," +
                    Csv(jobName) + "," +
                    Csv(f[3].Trim()) + "," +
                    Csv(f[4].Trim()) + "," +
                    Csv(f[5].Trim()) + "," +
                    Csv(f[6].Trim()) + "," +
                    Csv(f[7].Trim()) + "," +
                    Csv(f[8].Trim()) + "," +
                    duration.ToString(Inv) + "," +
                    (encoded ? ownNumber.ToString(Inv) : "") + "," +
                    parents.Count.ToString(Inv)
                );

                selectedTaskRows++;

                if (encoded)
                {
                    Dictionary<int, string> map;
                    if (!taskNumberMap.TryGetValue(jobName, out map))
                    {
                        map = new Dictionary<int, string>();
                        taskNumberMap.Add(jobName, map);
                    }

                    if (!map.ContainsKey(ownNumber))
                        map.Add(ownNumber, f[0].Trim());

                    AlibabaTaskEdgeCandidate candidate =
                        new AlibabaTaskEdgeCandidate();

                    candidate.JobName = jobName;
                    candidate.ChildTaskName = f[0].Trim();
                    candidate.ChildNumber = ownNumber;
                    candidate.ParentNumbers.AddRange(parents);
                    edgeCandidates.Add(candidate);
                }
            }
        }

        long edgeCount = 0;
        using (StreamWriter writer = NewWriter(edgesOutputPath))
        {
            writer.WriteLine(
                "job_name,parent_task_name,child_task_name," +
                "parent_task_number,child_task_number");

            foreach (AlibabaTaskEdgeCandidate candidate in edgeCandidates)
            {
                Dictionary<int, string> map;
                if (!taskNumberMap.TryGetValue(candidate.JobName, out map))
                    continue;

                foreach (int parentNumber in candidate.ParentNumbers)
                {
                    string parentTaskName;
                    if (!map.TryGetValue(parentNumber, out parentTaskName))
                        continue;

                    if (parentNumber == candidate.ChildNumber)
                        continue;

                    writer.WriteLine(
                        Csv(candidate.JobName) + "," +
                        Csv(parentTaskName) + "," +
                        Csv(candidate.ChildTaskName) + "," +
                        parentNumber.ToString(Inv) + "," +
                        candidate.ChildNumber.ToString(Inv)
                    );

                    edgeCount++;
                }
            }
        }

        AlibabaTaskStats stats = new AlibabaTaskStats();
        stats.JobsObserved = jobs.Count;
        stats.EligibleJobs = eligible.Count;
        stats.SelectedJobs = selected.Count;
        stats.SelectedPlannedInstances = selectedInstances;
        stats.SelectedTasks = selectedTaskRows;
        stats.DagEdges = edgeCount;
        return stats;
    }

    private static AlibabaFilterStats FilterInstancesReader(
        TextReader reader,
        HashSet<string> selectedJobs,
        string allOutputPath,
        string validOutputPath,
        string malformedOutputPath,
        string machineCountsOutputPath)
    {
        AlibabaFilterStats stats = new AlibabaFilterStats();
        Dictionary<string, long> machineCounts =
            new Dictionary<string, long>(StringComparer.Ordinal);

        using (StreamWriter allWriter = NewWriter(allOutputPath))
        using (StreamWriter validWriter = NewWriter(validOutputPath))
        using (StreamWriter malformedWriter = NewWriter(malformedOutputPath))
        {
            string header =
                "instance_name,task_name,job_name,task_type,status,start_time," +
                "end_time,machine_id,seq_no,total_seq_no,cpu_avg,cpu_max," +
                "mem_avg,mem_max,runtime_seconds,is_valid,validation_reason";

            allWriter.WriteLine(header);
            validWriter.WriteLine(header);

            string line;
            while ((line = reader.ReadLine()) != null)
            {
                stats.LinesRead++;
                if (line.Length == 0) continue;

                string first = GetFieldFast(line, 0);
                if (string.Equals(first, "instance_name", StringComparison.OrdinalIgnoreCase))
                    continue;

                string jobName = GetFieldFast(line, 2);
                if (jobName == null || !selectedJobs.Contains(jobName))
                {
                    if (stats.LinesRead % 5000000 == 0)
                        Console.WriteLine(
                            "Scanned {0:N0} batch-instance rows; matched {1:N0}...",
                            stats.LinesRead, stats.MatchedRows);
                    continue;
                }

                stats.MatchedRows++;
                string[] f = line.Split(',');

                if (f.Length < 14)
                {
                    stats.MalformedRows++;
                    malformedWriter.WriteLine(line);
                    continue;
                }

                long startTime = 0, endTime = 0;
                double cpuAvg = 0.0, cpuMax = 0.0, memAvg = 0.0, memMax = 0.0;

                List<string> reasons = new List<string>();

                bool statusOk = IsSuccessfulStatus(f[4]);
                if (!statusOk) reasons.Add("non_success_status");

                bool startOk = ParseLongFlexible(f[5], out startTime) && startTime >= 0;
                if (!startOk) reasons.Add("invalid_start_time");

                bool endOk = ParseLongFlexible(f[6], out endTime) &&
                             startOk && endTime > startTime;
                if (!endOk) reasons.Add("invalid_end_time");

                string machineId = f[7].Trim();
                bool machineOk = machineId.Length > 0;
                if (!machineOk) reasons.Add("missing_machine_id");

                bool cpuAvgOk = ParseDouble(f[10], out cpuAvg) && cpuAvg >= 0;
                if (!cpuAvgOk) reasons.Add("invalid_cpu_avg");

                bool cpuMaxOk = ParseDouble(f[11], out cpuMax) && cpuMax >= 0;
                if (!cpuMaxOk) reasons.Add("invalid_cpu_max");

                bool memAvgOk = ParseDouble(f[12], out memAvg) &&
                                memAvg >= 0 && memAvg <= 100;
                if (!memAvgOk) reasons.Add("invalid_mem_avg");

                bool memMaxOk = ParseDouble(f[13], out memMax) &&
                                memMax >= 0 && memMax <= 100;
                if (!memMaxOk) reasons.Add("invalid_mem_max");

                bool valid =
                    statusOk && startOk && endOk && machineOk &&
                    cpuAvgOk && cpuMaxOk && memAvgOk && memMaxOk;

                long runtime = startOk && endOk ? endTime - startTime : -1;
                string reason = valid ? "" : string.Join(";", reasons.ToArray());

                StringBuilder row = new StringBuilder(line.Length + 100);
                for (int i = 0; i < 14; i++)
                {
                    if (i > 0) row.Append(',');
                    row.Append(Csv(f[i].Trim()));
                }

                row.Append(',');
                row.Append(runtime.ToString(Inv));
                row.Append(',');
                row.Append(valid ? "true" : "false");
                row.Append(',');
                row.Append(Csv(reason));

                allWriter.WriteLine(row.ToString());

                if (valid)
                {
                    validWriter.WriteLine(row.ToString());
                    stats.ValidRows++;

                    long current;
                    if (!machineCounts.TryGetValue(machineId, out current))
                        current = 0;
                    machineCounts[machineId] = current + 1;
                }
                else
                {
                    stats.InvalidRows++;
                }

                if (stats.LinesRead % 5000000 == 0)
                    Console.WriteLine(
                        "Scanned {0:N0} batch-instance rows; matched {1:N0}; valid {2:N0}...",
                        stats.LinesRead, stats.MatchedRows, stats.ValidRows);
            }
        }

        using (StreamWriter writer = NewWriter(machineCountsOutputPath))
        {
            writer.WriteLine("machine_id,valid_instance_count");
            foreach (KeyValuePair<string, long> kv in
                machineCounts.OrderByDescending(k => k.Value).ThenBy(k => k.Key))
            {
                writer.WriteLine(Csv(kv.Key) + "," + kv.Value.ToString(Inv));
            }
        }

        stats.DistinctMachines = machineCounts.Count;
        return stats;
    }

    private static Process StartTarProcess(
        string tarExecutable,
        string archivePath,
        string memberName,
        StringBuilder errorText)
    {
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = tarExecutable;
        psi.Arguments =
            "-xOf \"" + archivePath.Replace("\"", "\\\"") +
            "\" \"" + memberName.Replace("\"", "\\\"") + "\"";
        psi.UseShellExecute = false;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        psi.CreateNoWindow = true;

        Process process = new Process();
        process.StartInfo = psi;
        process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
        {
            if (e.Data != null)
            {
                lock (errorText)
                {
                    errorText.AppendLine(e.Data);
                }
            }
        };

        if (!process.Start())
            throw new InvalidOperationException("Could not start tar.");

        process.BeginErrorReadLine();
        return process;
    }

    private static HashSet<string> LoadIds(string path)
    {
        HashSet<string> ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (string raw in File.ReadLines(path))
        {
            string value = (raw ?? "").Trim();
            if (value.Length > 0 && !value.EndsWith("_id", StringComparison.OrdinalIgnoreCase))
                ids.Add(value);
        }
        return ids;
    }

    public static AlibabaFilterStats FilterInstancesFile(
        string csvPath,
        string selectedJobsPath,
        string allOutputPath,
        string validOutputPath,
        string malformedOutputPath,
        string machineCountsOutputPath)
    {
        HashSet<string> selectedJobs = LoadIds(selectedJobsPath);
        using (StreamReader reader = new StreamReader(
            csvPath, Encoding.UTF8, true, 4 * 1024 * 1024))
        {
            return FilterInstancesReader(
                reader, selectedJobs, allOutputPath, validOutputPath,
                malformedOutputPath, machineCountsOutputPath);
        }
    }

    public static AlibabaFilterStats FilterInstancesTar(
        string tarExecutable,
        string archivePath,
        string memberName,
        string selectedJobsPath,
        string allOutputPath,
        string validOutputPath,
        string malformedOutputPath,
        string machineCountsOutputPath)
    {
        HashSet<string> selectedJobs = LoadIds(selectedJobsPath);
        StringBuilder errorText = new StringBuilder();

        using (Process process = StartTarProcess(
            tarExecutable, archivePath, memberName, errorText))
        {
            AlibabaFilterStats stats;
            using (StreamReader reader = process.StandardOutput)
            {
                stats = FilterInstancesReader(
                    reader, selectedJobs, allOutputPath, validOutputPath,
                    malformedOutputPath, machineCountsOutputPath);
            }

            process.WaitForExit();
            if (process.ExitCode != 0)
            {
                throw new InvalidOperationException(
                    "tar failed while reading batch_instance. " +
                    errorText.ToString());
            }

            return stats;
        }
    }

    public static AlibabaMachineStats PrepareMachines(
        string machineMetaCsv,
        string machineCountsPath,
        string selectedMachineIdsPath,
        string allReferencedEventsPath,
        string selectedEventsPath,
        string capacitiesPath,
        int machineLimit)
    {
        Dictionary<string, long> counts =
            new Dictionary<string, long>(StringComparer.Ordinal);

        foreach (string line in File.ReadLines(machineCountsPath))
        {
            if (string.IsNullOrWhiteSpace(line)) continue;
            string[] f = line.Split(',');
            if (IsHeader(f, "machine_id")) continue;
            if (f.Length < 2) continue;

            long count;
            if (ParseLongFlexible(f[1], out count))
                counts[f[0].Trim()] = count;
        }

        Dictionary<string, List<AlibabaMetaRow>> meta =
            new Dictionary<string, List<AlibabaMetaRow>>(StringComparer.Ordinal);

        foreach (string line in File.ReadLines(machineMetaCsv))
        {
            if (string.IsNullOrWhiteSpace(line)) continue;
            string[] f = line.Split(',');
            if (IsHeader(f, "machine_id")) continue;
            if (f.Length < 7) continue;

            string machineId = f[0].Trim();
            if (!counts.ContainsKey(machineId)) continue;

            long timestamp;
            if (!ParseLongFlexible(f[1], out timestamp))
                timestamp = -1;

            List<AlibabaMetaRow> rows;
            if (!meta.TryGetValue(machineId, out rows))
            {
                rows = new List<AlibabaMetaRow>();
                meta.Add(machineId, rows);
            }

            AlibabaMetaRow row = new AlibabaMetaRow();
            row.Fields = f.Take(7).Select(x => x.Trim()).ToArray();
            row.TimeStamp = timestamp;
            rows.Add(row);
        }

        List<string> selected = counts
            .Where(kv => meta.ContainsKey(kv.Key))
            .OrderByDescending(kv => kv.Value)
            .ThenBy(kv => kv.Key)
            .Take(machineLimit)
            .Select(kv => kv.Key)
            .ToList();

        HashSet<string> selectedSet =
            new HashSet<string>(selected, StringComparer.Ordinal);

        using (StreamWriter writer = NewWriter(selectedMachineIdsPath))
        {
            foreach (string machineId in selected)
                writer.WriteLine(machineId);
        }

        long allReferencedRows = 0;
        using (StreamWriter writer = NewWriter(allReferencedEventsPath))
        {
            writer.WriteLine(
                "machine_id,time_stamp,failure_domain_1,failure_domain_2," +
                "cpu_num,mem_size,status,valid_instance_count");

            foreach (KeyValuePair<string, List<AlibabaMetaRow>> kv in
                meta.OrderBy(k => k.Key))
            {
                foreach (AlibabaMetaRow row in kv.Value.OrderBy(r => r.TimeStamp))
                {
                    writer.WriteLine(
                        string.Join(",", row.Fields.Select(Csv).ToArray()) + "," +
                        counts[kv.Key].ToString(Inv));
                    allReferencedRows++;
                }
            }
        }

        long selectedRows = 0;
        using (StreamWriter writer = NewWriter(selectedEventsPath))
        {
            writer.WriteLine(
                "machine_id,time_stamp,failure_domain_1,failure_domain_2," +
                "cpu_num,mem_size,status,valid_instance_count");

            foreach (string machineId in selected)
            {
                foreach (AlibabaMetaRow row in meta[machineId].OrderBy(r => r.TimeStamp))
                {
                    writer.WriteLine(
                        string.Join(",", row.Fields.Select(Csv).ToArray()) + "," +
                        counts[machineId].ToString(Inv));
                    selectedRows++;
                }
            }
        }

        using (StreamWriter writer = NewWriter(capacitiesPath))
        {
            writer.WriteLine(
                "machine_id,time_stamp,cpu_num,mem_size,status," +
                "failure_domain_1,failure_domain_2,valid_instance_count");

            foreach (string machineId in selected)
            {
                AlibabaMetaRow latestValid = meta[machineId]
                    .Where(r =>
                    {
                        long cpu;
                        double mem;
                        return ParseLongFlexible(r.Fields[4], out cpu) &&
                               cpu > 0 &&
                               ParseDouble(r.Fields[5], out mem) &&
                               mem >= 0 && mem <= 100;
                    })
                    .OrderByDescending(r => r.TimeStamp)
                    .FirstOrDefault();

                if (latestValid == null)
                    latestValid = meta[machineId]
                        .OrderByDescending(r => r.TimeStamp)
                        .First();

                string[] f = latestValid.Fields;
                writer.WriteLine(
                    Csv(f[0]) + "," +
                    Csv(f[1]) + "," +
                    Csv(f[4]) + "," +
                    Csv(f[5]) + "," +
                    Csv(f[6]) + "," +
                    Csv(f[2]) + "," +
                    Csv(f[3]) + "," +
                    counts[machineId].ToString(Inv)
                );
            }
        }

        AlibabaMachineStats stats = new AlibabaMachineStats();
        stats.MachinesReferencedByValidInstances = counts.Count;
        stats.MachinesFoundInMetadata = meta.Count;
        stats.MachinesSelectedForUsage = selected.Count;
        stats.MetadataRowsForReferencedMachines = allReferencedRows;
        stats.MetadataRowsForSelectedMachines = selectedRows;
        return stats;
    }

    private static AlibabaFilterStats FilterUsageReader(
        TextReader reader,
        HashSet<string> selectedMachines,
        string usageOutputPath,
        string usageSummaryPath,
        string malformedOutputPath)
    {
        AlibabaFilterStats stats = new AlibabaFilterStats();
        Dictionary<string, AlibabaUsageAggregate> aggregates =
            new Dictionary<string, AlibabaUsageAggregate>(StringComparer.Ordinal);

        using (StreamWriter writer = NewWriter(usageOutputPath))
        using (StreamWriter malformedWriter = NewWriter(malformedOutputPath))
        {
            writer.WriteLine(
                "machine_id,time_stamp,cpu_util_percent,mem_util_percent," +
                "mem_gps,mkpi,net_in,net_out,disk_io_percent,is_valid");

            string line;
            while ((line = reader.ReadLine()) != null)
            {
                stats.LinesRead++;
                if (line.Length == 0) continue;

                string machineId = GetFieldFast(line, 0);
                if (string.Equals(machineId, "machine_id", StringComparison.OrdinalIgnoreCase))
                    continue;

                if (machineId == null || !selectedMachines.Contains(machineId))
                {
                    if (stats.LinesRead % 5000000 == 0)
                        Console.WriteLine(
                            "Scanned {0:N0} machine-usage rows; matched {1:N0}...",
                            stats.LinesRead, stats.MatchedRows);
                    continue;
                }

                stats.MatchedRows++;
                string[] f = line.Split(',');
                if (f.Length < 9)
                {
                    stats.MalformedRows++;
                    malformedWriter.WriteLine(line);
                    continue;
                }

                double timestamp = 0.0, cpu = 0.0, mem = 0.0, memGps = 0.0, mkpi = 0.0, netIn = 0.0, netOut = 0.0, disk = 0.0;
                bool valid =
                    ParseDouble(f[1], out timestamp) && timestamp >= 0 &&
                    ParseDouble(f[2], out cpu) && cpu >= 0 && cpu <= 100 &&
                    ParseDouble(f[3], out mem) && mem >= 0 && mem <= 100 &&
                    ParseDouble(f[4], out memGps) && memGps >= 0 && memGps <= 100 &&
                    ParseDouble(f[5], out mkpi) && mkpi >= 0 &&
                    ParseDouble(f[6], out netIn) && netIn >= 0 && netIn <= 100 &&
                    ParseDouble(f[7], out netOut) && netOut >= 0 && netOut <= 100 &&
                    ParseDouble(f[8], out disk) && disk >= 0 && disk <= 100;

                StringBuilder row = new StringBuilder(line.Length + 10);
                for (int i = 0; i < 9; i++)
                {
                    if (i > 0) row.Append(',');
                    row.Append(Csv(f[i].Trim()));
                }
                row.Append(',');
                row.Append(valid ? "true" : "false");
                writer.WriteLine(row.ToString());

                AlibabaUsageAggregate agg;
                if (!aggregates.TryGetValue(machineId, out agg))
                {
                    agg = new AlibabaUsageAggregate();
                    aggregates.Add(machineId, agg);
                }

                agg.Rows++;

                if (valid)
                {
                    stats.ValidRows++;
                    agg.ValidRows++;
                    agg.CpuSum += cpu;
                    agg.MemSum += mem;
                    agg.NetInSum += netIn;
                    agg.NetOutSum += netOut;
                    agg.DiskSum += disk;
                }
                else
                {
                    stats.InvalidRows++;
                }

                if (stats.LinesRead % 5000000 == 0)
                    Console.WriteLine(
                        "Scanned {0:N0} machine-usage rows; matched {1:N0}; valid {2:N0}...",
                        stats.LinesRead, stats.MatchedRows, stats.ValidRows);
            }
        }

        using (StreamWriter writer = NewWriter(usageSummaryPath))
        {
            writer.WriteLine(
                "machine_id,row_count,valid_row_count,mean_cpu_util_percent," +
                "mean_mem_util_percent,mean_net_in,mean_net_out," +
                "mean_disk_io_percent");

            foreach (KeyValuePair<string, AlibabaUsageAggregate> kv in
                aggregates.OrderBy(k => k.Key))
            {
                AlibabaUsageAggregate a = kv.Value;
                double denom = a.ValidRows > 0 ? a.ValidRows : 1;

                writer.WriteLine(
                    Csv(kv.Key) + "," +
                    a.Rows.ToString(Inv) + "," +
                    a.ValidRows.ToString(Inv) + "," +
                    (a.ValidRows > 0 ? (a.CpuSum / denom).ToString("R", Inv) : "") + "," +
                    (a.ValidRows > 0 ? (a.MemSum / denom).ToString("R", Inv) : "") + "," +
                    (a.ValidRows > 0 ? (a.NetInSum / denom).ToString("R", Inv) : "") + "," +
                    (a.ValidRows > 0 ? (a.NetOutSum / denom).ToString("R", Inv) : "") + "," +
                    (a.ValidRows > 0 ? (a.DiskSum / denom).ToString("R", Inv) : "")
                );
            }
        }

        stats.DistinctMachines = aggregates.Count;
        return stats;
    }

    public static AlibabaFilterStats FilterUsageFile(
        string csvPath,
        string selectedMachineIdsPath,
        string usageOutputPath,
        string usageSummaryPath,
        string malformedOutputPath)
    {
        HashSet<string> machines = LoadIds(selectedMachineIdsPath);
        using (StreamReader reader = new StreamReader(
            csvPath, Encoding.UTF8, true, 4 * 1024 * 1024))
        {
            return FilterUsageReader(
                reader, machines, usageOutputPath,
                usageSummaryPath, malformedOutputPath);
        }
    }

    public static AlibabaFilterStats FilterUsageTar(
        string tarExecutable,
        string archivePath,
        string memberName,
        string selectedMachineIdsPath,
        string usageOutputPath,
        string usageSummaryPath,
        string malformedOutputPath)
    {
        HashSet<string> machines = LoadIds(selectedMachineIdsPath);
        StringBuilder errorText = new StringBuilder();

        using (Process process = StartTarProcess(
            tarExecutable, archivePath, memberName, errorText))
        {
            AlibabaFilterStats stats;
            using (StreamReader reader = process.StandardOutput)
            {
                stats = FilterUsageReader(
                    reader, machines, usageOutputPath,
                    usageSummaryPath, malformedOutputPath);
            }

            process.WaitForExit();
            if (process.ExitCode != 0)
            {
                throw new InvalidOperationException(
                    "tar failed while reading machine_usage. " +
                    errorText.ToString());
            }

            return stats;
        }
    }
}
'@

Write-Step "Loading the high-speed streaming filter"
Add-Type -TypeDefinition $csharp -Language CSharp

$SelectedJobs = Join-Path $OutputDirectory "selected_job_ids.txt"
$RealJobs = Join-Path $OutputDirectory "real_jobs.csv"
$RealTasks = Join-Path $OutputDirectory "real_tasks.csv"
$RealEdges = Join-Path $OutputDirectory "real_dag_edges.csv"

$InstancesAll = Join-Path $OutputDirectory "real_instances_all.csv"
$InstancesValid = Join-Path $OutputDirectory "real_instances.csv"
$MalformedInstances = Join-Path $OutputDirectory "malformed_instance_rows.txt"
$MachineCounts = Join-Path $OutputDirectory "machine_instance_counts.csv"

$SelectedMachineIds = Join-Path $OutputDirectory "selected_machine_ids.txt"
$MachinesAllEvents = Join-Path $OutputDirectory "real_machines_all_referenced_events.csv"
$MachinesSelectedEvents = Join-Path $OutputDirectory "real_machines.csv"
$MachineCapacities = Join-Path $OutputDirectory "real_machine_capacities.csv"

$MachineUsageOutput = Join-Path $OutputDirectory "real_machine_usage.csv"
$MachineUsageSummary = Join-Path $OutputDirectory "real_machine_usage_summary.csv"
$MalformedUsage = Join-Path $OutputDirectory "malformed_machine_usage_rows.txt"

if ($Stage -eq "1" -or $Stage -eq "all") {
    if (-not $BatchInstanceCsv -and -not $BatchInstanceArchive) {
        throw "Neither batch_instance.csv nor a batch_instance .tar/.tar.gz archive was found."
    }

    Write-Step "Stage 1A: Selecting complete real jobs and reconstructing DAG edges"

    $taskStats = [AlibabaSubsetBuilderV1]::PrepareTasks(
        $TaskCsv,
        $SelectedJobs,
        $RealJobs,
        $RealTasks,
        $RealEdges,
        $MaximumJobs,
        $MaximumPlannedInstances
    )

    $taskStats |
        ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $OutputDirectory "task_subset_stats.json") -Encoding UTF8

    Write-Host "Selected jobs:              $($taskStats.SelectedJobs)" -ForegroundColor Green
    Write-Host "Selected task rows:         $($taskStats.SelectedTasks)"
    Write-Host "Planned instance count:     $($taskStats.SelectedPlannedInstances)"
    Write-Host "Reconstructed DAG edges:    $($taskStats.DagEdges)"

    if ($taskStats.SelectedJobs -eq 0) {
        throw "No eligible jobs were selected. Stop and inspect task_subset_stats.json."
    }

    Write-Step "Stage 1B: Streaming the real batch-instance data"

    $tmpAll = $InstancesAll + ".partial"
    $tmpValid = $InstancesValid + ".partial"
    $tmpMalformed = $MalformedInstances + ".partial"
    $tmpCounts = $MachineCounts + ".partial"

    Remove-Item -LiteralPath $tmpAll, $tmpValid, $tmpMalformed, $tmpCounts `
        -Force -ErrorAction SilentlyContinue

    if ($BatchInstanceCsv) {
        $instanceStats = [AlibabaSubsetBuilderV1]::FilterInstancesFile(
            $BatchInstanceCsv,
            $SelectedJobs,
            $tmpAll,
            $tmpValid,
            $tmpMalformed,
            $tmpCounts
        )
    } else {
        if (-not $TarExecutable) {
            throw "Windows tar.exe was not found."
        }

        $batchMember = Get-FirstTarMember `
            -TarExecutable $TarExecutable `
            -ArchivePath $BatchInstanceArchive `
            -ExpectedLeafName "batch_instance.csv"

        Write-Host "Reading archive member: $batchMember"

        $instanceStats = [AlibabaSubsetBuilderV1]::FilterInstancesTar(
            $TarExecutable,
            $BatchInstanceArchive,
            $batchMember,
            $SelectedJobs,
            $tmpAll,
            $tmpValid,
            $tmpMalformed,
            $tmpCounts
        )
    }

    Move-Item -LiteralPath $tmpAll -Destination $InstancesAll -Force
    Move-Item -LiteralPath $tmpValid -Destination $InstancesValid -Force
    Move-Item -LiteralPath $tmpMalformed -Destination $MalformedInstances -Force
    Move-Item -LiteralPath $tmpCounts -Destination $MachineCounts -Force

    $instanceStats |
        ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $OutputDirectory "instance_subset_stats.json") -Encoding UTF8

    Write-Host "Matched instance rows:      $($instanceStats.MatchedRows)" -ForegroundColor Green
    Write-Host "Valid instance rows:        $($instanceStats.ValidRows)"
    Write-Host "Invalid instance rows:      $($instanceStats.InvalidRows)"
    Write-Host "Distinct source machines:   $($instanceStats.DistinctMachines)"

    if ($instanceStats.ValidRows -eq 0) {
        throw "No valid instance rows were created. Inspect instance_subset_stats.json."
    }

    Write-Step "Stage 1C: Linking real machine metadata"

    $machineStats = [AlibabaSubsetBuilderV1]::PrepareMachines(
        $MachineMetaCsv,
        $MachineCounts,
        $SelectedMachineIds,
        $MachinesAllEvents,
        $MachinesSelectedEvents,
        $MachineCapacities,
        $MachineUsageMachineLimit
    )

    $machineStats |
        ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $OutputDirectory "machine_subset_stats.json") -Encoding UTF8

    Write-Host "Machines referenced:        $($machineStats.MachinesReferencedByValidInstances)"
    Write-Host "Machines found in metadata: $($machineStats.MachinesFoundInMetadata)"
    Write-Host "Machines selected for usage:$($machineStats.MachinesSelectedForUsage)" -ForegroundColor Green

    $stage1Manifest = [ordered]@{
        dataset = "Alibaba Cluster Trace v2018 real subset"
        created_at = (Get-Date).ToString("o")
        source_batch_task = $TaskCsv
        source_batch_instance = $(if ($BatchInstanceCsv) { $BatchInstanceCsv } else { $BatchInstanceArchive })
        source_machine_meta = $MachineMetaCsv
        parameters = [ordered]@{
            maximum_jobs = $MaximumJobs
            maximum_planned_instances = $MaximumPlannedInstances
            machine_usage_machine_limit = $MachineUsageMachineLimit
        }
        task_stats = $taskStats
        instance_stats = $instanceStats
        machine_stats = $machineStats
        outputs = [ordered]@{
            real_jobs = $RealJobs
            real_tasks = $RealTasks
            real_dag_edges = $RealEdges
            real_instances_all = $InstancesAll
            real_instances_valid = $InstancesValid
            real_machine_capacities = $MachineCapacities
            selected_job_ids = $SelectedJobs
            selected_machine_ids = $SelectedMachineIds
        }
    }

    $stage1Manifest |
        ConvertTo-Json -Depth 10 |
        Set-Content -LiteralPath (Join-Path $OutputDirectory "real_trace_manifest_stage1.json") -Encoding UTF8

    Set-Content -LiteralPath (Join-Path $OutputDirectory "STAGE1_COMPLETE.txt") `
        -Value ("Stage 1 completed at " + (Get-Date).ToString("o")) `
        -Encoding UTF8
}

if ($Stage -eq "2" -or $Stage -eq "all") {
    if (-not (Test-Path -LiteralPath $SelectedMachineIds -PathType Leaf)) {
        throw "selected_machine_ids.txt is missing. Run Stage 1 first."
    }

    if (-not $MachineUsageCsv -and -not $MachineUsageArchive) {
        throw "Neither machine_usage.csv nor a machine_usage .tar/.tar.gz archive was found."
    }

    Write-Step "Stage 2: Streaming machine-usage records for the selected machines"

    $tmpUsage = $MachineUsageOutput + ".partial"
    $tmpUsageSummary = $MachineUsageSummary + ".partial"
    $tmpMalformedUsage = $MalformedUsage + ".partial"

    Remove-Item -LiteralPath $tmpUsage, $tmpUsageSummary, $tmpMalformedUsage `
        -Force -ErrorAction SilentlyContinue

    if ($MachineUsageCsv) {
        $usageStats = [AlibabaSubsetBuilderV1]::FilterUsageFile(
            $MachineUsageCsv,
            $SelectedMachineIds,
            $tmpUsage,
            $tmpUsageSummary,
            $tmpMalformedUsage
        )
    } else {
        if (-not $TarExecutable) {
            throw "Windows tar.exe was not found."
        }

        $usageMember = Get-FirstTarMember `
            -TarExecutable $TarExecutable `
            -ArchivePath $MachineUsageArchive `
            -ExpectedLeafName "machine_usage.csv"

        Write-Host "Reading archive member: $usageMember"

        $usageStats = [AlibabaSubsetBuilderV1]::FilterUsageTar(
            $TarExecutable,
            $MachineUsageArchive,
            $usageMember,
            $SelectedMachineIds,
            $tmpUsage,
            $tmpUsageSummary,
            $tmpMalformedUsage
        )
    }

    Move-Item -LiteralPath $tmpUsage -Destination $MachineUsageOutput -Force
    Move-Item -LiteralPath $tmpUsageSummary -Destination $MachineUsageSummary -Force
    Move-Item -LiteralPath $tmpMalformedUsage -Destination $MalformedUsage -Force

    $usageStats |
        ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $OutputDirectory "machine_usage_subset_stats.json") -Encoding UTF8

    Write-Host "Matched machine-usage rows: $($usageStats.MatchedRows)" -ForegroundColor Green
    Write-Host "Valid machine-usage rows:   $($usageStats.ValidRows)"
    Write-Host "Invalid usage rows retained:$($usageStats.InvalidRows)"
    Write-Host "Machines with usage data:   $($usageStats.DistinctMachines)"

    $stage2Manifest = [ordered]@{
        dataset = "Alibaba Cluster Trace v2018 machine-usage subset"
        created_at = (Get-Date).ToString("o")
        source_machine_usage = $(if ($MachineUsageCsv) { $MachineUsageCsv } else { $MachineUsageArchive })
        selected_machine_ids = $SelectedMachineIds
        usage_stats = $usageStats
        outputs = [ordered]@{
            real_machine_usage = $MachineUsageOutput
            real_machine_usage_summary = $MachineUsageSummary
        }
    }

    $stage2Manifest |
        ConvertTo-Json -Depth 10 |
        Set-Content -LiteralPath (Join-Path $OutputDirectory "real_trace_manifest_stage2.json") -Encoding UTF8

    Set-Content -LiteralPath (Join-Path $OutputDirectory "STAGE2_COMPLETE.txt") `
        -Value ("Stage 2 completed at " + (Get-Date).ToString("o")) `
        -Encoding UTF8
}

$readme = @"
ALIBABA CLUSTER TRACE V2018: REAL SUBSET

This folder contains real records filtered from the original Alibaba trace.
No simulated records are generated by this tool.

CORE FILES
----------
real_jobs.csv
    Selected complete jobs and job-level characteristics.

real_tasks.csv
    All task rows for the selected jobs.

real_dag_edges.csv
    Parent-child dependencies reconstructed from DAG-encoded task names.

real_instances_all.csv
    All matched instance rows, including invalid and unsuccessful records,
    with an is_valid field and validation reason.

real_instances.csv
    Valid successful instance records for trace replay and calibration.

real_machine_capacities.csv
    Latest valid capacity record for each of the selected machine-usage machines.

real_machines.csv
    Machine metadata events for the selected machine-usage machines.

real_machine_usage.csv
    Real machine time-series records for selected machines. Created by Stage 2.

QUALITY AND AUDIT FILES
-----------------------
selected_job_ids.txt
selected_machine_ids.txt
machine_instance_counts.csv
real_machine_usage_summary.csv
malformed_instance_rows.txt
malformed_machine_usage_rows.txt
task_subset_stats.json
instance_subset_stats.json
machine_subset_stats.json
machine_usage_subset_stats.json
real_trace_manifest_stage1.json
real_trace_manifest_stage2.json

IMPORTANT
---------
The script reads batch_instance and machine_usage directly from their archives.
It does not extract the complete decompressed CSV files.

Communication volume, processor power, DVFS levels and DPM transition costs are
not supplied by Alibaba Cluster Trace v2018 and are not generated here.
"@

Set-Content -LiteralPath (Join-Path $OutputDirectory "README_REAL_SUBSET.txt") `
    -Value $readme -Encoding UTF8

Write-Step "Finished"
Write-Host "Output folder:" -ForegroundColor Green
Write-Host $OutputDirectory
Write-Host ""
Write-Host "Files currently present:"
Get-ChildItem -LiteralPath $OutputDirectory -File |
    Sort-Object Name |
    Select-Object Name, @{Name="Size_MB";Expression={[math]::Round($_.Length / 1MB, 2)}} |
    Format-Table -AutoSize

Start-Process explorer.exe $OutputDirectory
