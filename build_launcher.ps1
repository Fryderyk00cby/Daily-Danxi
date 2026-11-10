$ErrorActionPreference = 'Stop'
$readerRoot = $PSScriptRoot
$readerPython = (& python -c "import sys; from pathlib import Path; print(Path(sys.executable).with_name('pythonw.exe'))").Trim()
$readerPythonLiteral = $readerPython.Replace('"', '""')
$readerSource = @"
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
class DanXiDaily {
    [STAThread]
    static void Main() {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string script = Path.Combine(root, "local_web.py");
            if (!File.Exists(script)) throw new Exception("Please keep this launcher in the project folder.");
            string python = @"$readerPythonLiteral";
            if (!File.Exists(python)) python = "pythonw.exe";
            Process.Start(new ProcessStartInfo {
                FileName = python,
                Arguments = "\"" + script + "\"",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true
            });
        } catch (Exception error) {
            MessageBox.Show("Unable to start DanXi Daily.\n" + error.Message, "DanXi Daily", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
"@
$readerOutput = Join-Path $readerRoot 'DanXiDaily.exe'
Add-Type -TypeDefinition $readerSource -ReferencedAssemblies System.Windows.Forms -OutputAssembly $readerOutput -OutputType WindowsApplication
Write-Output "Built $readerOutput"
