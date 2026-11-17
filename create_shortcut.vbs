Option Explicit
Dim shell, fs, root, link
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
Set link = shell.CreateShortcut(shell.SpecialFolders("Desktop") & "\WG Solar Lab.lnk")
link.TargetPath = root & "\start_windows.bat"
link.WorkingDirectory = root
link.Description = "WG Solar Lab"
link.IconLocation = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\shell32.dll,13"
link.WindowStyle = 1
link.Save
