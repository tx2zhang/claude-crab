' Silent launcher for the crab pet. Double-click to start.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\Tian\.claude\pet"
sh.Run """C:\Users\Tian\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"" pet.py", 0, False
