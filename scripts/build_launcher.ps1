$ErrorActionPreference = 'Stop'
$readerRoot = Split-Path -Parent $PSScriptRoot
$readerData = Join-Path $readerRoot 'local_data'
New-Item -ItemType Directory -Path $readerData -Force | Out-Null
$readerPython = Join-Path $readerRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $readerPython)) {
    $readerPython = (& python -c 'import sys; print(sys.executable)').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Python not found. Run setup.cmd first.' }
}
$readerPythonw = Join-Path (Split-Path -Parent $readerPython) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $readerPythonw)) { throw 'pythonw.exe not found.' }
Set-Content -LiteralPath (Join-Path $readerData 'python_path.txt') -Value $readerPythonw -Encoding UTF8
$readerSource = @'
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
class DanXiDaily {
    [STAThread]
    static void Main() {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string script = Path.Combine(root, "main.py");
            if (!File.Exists(script)) throw new Exception("Keep DanXiDaily.exe in the project folder. You can create a desktop shortcut.");
            string python = Path.Combine(root, @".venv\Scripts\pythonw.exe");
            if (!File.Exists(python)) {
                string config = Path.Combine(root, @"local_data\python_path.txt");
                if (!File.Exists(config)) throw new Exception("Please run setup.cmd once before starting.");
                python = File.ReadAllText(config).Trim();
            }
            if (!File.Exists(python)) throw new Exception("Python was moved or removed. Run setup.cmd again.");
            Process.Start(new ProcessStartInfo {
                FileName = python,
                Arguments = "\"" + script + "\"",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true
            });
        } catch (Exception error) {
            MessageBox.Show(error.Message, "DanXi Daily", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
'@
$readerBuild = Join-Path $readerData ('launcher-' + [Guid]::NewGuid().ToString('N') + '.exe')
$readerOutput = Join-Path $readerRoot 'DanXiDaily.exe'
Add-Type -TypeDefinition $readerSource -ReferencedAssemblies System.Windows.Forms -OutputAssembly $readerBuild -OutputType WindowsApplication
Move-Item -LiteralPath $readerBuild -Destination $readerOutput -Force
Write-Output 'Launcher ready: DanXiDaily.exe'
