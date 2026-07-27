' Silent launcher for the dragon variant of the pet.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\Tian\.claude\pet"
sh.Run """C:\Users\Tian\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"" pet.py --dragon", 0, False
