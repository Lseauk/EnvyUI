# EnvyUI Changelog

**EnvyUI v1.0.4**

-- **Build Exe** Reworked launcher approach

The Build EXE function has been rebuilt from the ground up. Previously it used PyInstaller to bundle a self-contained Python environment inside the exe, which caused downloads to behave differently from the batch file (no live progress, grey scrolling text instead of the normal coloured download panel).

The exe now works as a small launcher that uses your existing Python installation — the same one the batch file uses — so downloads are identical in both. The build also produces EnvyUI.lnk alongside the exe.

To launch / pin to Start: use EnvyUI.exe (double-click or right-click → Pin to Start in File Explorer)
To pin to taskbar: right-click EnvyUI.lnk → Pin to taskbar (using the exe directly causes two icons)
Updates: in most cases only envy_launcher.py needs replacing — no rebuild required

**EnvyUI v1.0.3**

-- **Added Back Button** When using Browse by Category and you select a genre, title, season, episodes instead of having to start from scratch you can now go back, please note if you've already selected episodes you will have to reselect them again if you use the back button.

-- **Browse by Category Service Updates** TVNZ, 10Play should now return correct results numbers, also added RTE Browse by Category option.

-- **UI Improvements** Minor changes to the look and feel of the app.

-- **Help Page** Updates to the help page.

-- **Build Exe** Now shows the on the log page what is happening and any errors if any happen.


**EnvyUI v1.0.2**

-- **Service Buttons** Changed the main service button to 4 rows instead of three allowing for 28 services.
You can adjust the size of the main service buttons box by adjust the height by searching for this 'svc_scroll.setFixedHeight(125)' in the envy_launcher.py file you can also adjust the number of rows and columns of buttons by searching for 'Populate service buttons for the given page' again in the envy_launcher file and changing the rows and column numbers to your liking, if you add more than 28 buttons it will create a new section automatically, with the page indicators.  

-- **IMDBApi Error in Download Log Panel** imdbapi.dev is down or unavailable, which will show as an error when downloading, while this does not affect the actual download we added a fix for this, see the help page of the app to address this issue.
Also added an indicator to the app to show which metadata service is up or has a valid api key when needed, more details can be found in the app help page.

-- **App Height Adjustment** For small screens you can adjust the height of the app please the help page of the app on how to do this.

-- **BBC iPlayer Browse by category results** Improved the number of returned results when using browse by category, it was limited to 100 but should now return all results.

-- **UI Improvements**
Some minor EnvyUI improvements

  
**EnvyUI v1.0.1**

-- **Service Buttons** Moved services from the extended service panel to main service panel.
The newly added services now support browse by category and keyword search and all other main download options.

-- **UI Improvements** 
Made some changes to the look and feel of the app.

-- **Help Page**
Updates to the help page with some new config options.
