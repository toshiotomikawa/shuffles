' Shuffles compatibility forwarder for an obsolete context-menu command.
Option Explicit
Dim shell, fso, base, reader, python, script, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
If WScript.Arguments.Count <> 1 Then WScript.Quit 1
base = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Shuffles")
script = fso.BuildPath(base, "shuffles.py")
If Not fso.FileExists(fso.BuildPath(base, "python-path.txt")) Then
    MsgBox "The installed Shuffles runtime is missing.", 16, "Shuffles"
    WScript.Quit 1
End If
Set reader = fso.OpenTextFile(fso.BuildPath(base, "python-path.txt"), 1)
python = Trim(reader.ReadAll)
reader.Close
python = fso.BuildPath(fso.GetParentFolderName(python), "pythonw.exe")
cmd = """" & python & """ """ & script & """ """ & WScript.Arguments(0) & "\."""
WScript.Quit shell.Run(cmd, 0, True)
