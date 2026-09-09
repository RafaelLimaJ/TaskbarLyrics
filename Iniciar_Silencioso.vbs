' TaskbarLyrics - Startup Autom?tico
Set WshShell = CreateObject("WScript.Shell")
scriptDir = "c:\Users\rafae\Desktop\PASTA DAS PASTAS\TaskbarLyrics"
pythonw = "C:\Users\rafae\AppData\Local\Programs\Python\Python312\pythonw.exe"

WshShell.CurrentDirectory = scriptDir
WshShell.Run """" & pythonw & """ app.py", 0, False
