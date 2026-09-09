Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

WshShell.CurrentDirectory = scriptDir
WshShell.Run "cmd /c Iniciar_Letras.bat", 0, False
