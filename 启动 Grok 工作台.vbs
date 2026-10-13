Set shell = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
pyw = shell.ExpandEnvironmentStrings("%LocalAppData%") & "\Programs\Python\Launcher\pyw.exe"
If Not fso.FileExists(pyw) Then
  pyw = shell.ExpandEnvironmentStrings("%LocalAppData%") & "\Programs\Python\Python312\pythonw.exe"
End If
shell.Run """" & pyw & """ -3 """ & folder & "\app.py""", 0, False
