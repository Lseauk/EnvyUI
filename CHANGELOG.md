# EnvyUI Changelog

**EnvyUI v1.0.5**

-- **Downloads now organised into Plex / Jellyfin / Kodi compatible folder structure**
TV episodes go into `Show Name (Year)/Season 01/` and movies into `Movie Name (Year)/` automatically. Previously all files landed flat in the Downloads folder. The show folder always uses the series premiere year, so all seasons of a show stay in the same top-level folder regardless of which season you download first.

-- **IMDB metadata now correctly tags downloaded files**
Downloaded MKV files were not being tagged with their IMDB ID even when the metadata lookup found a match. The IMDB ID was being returned by the provider but silently discarded. This is now fixed.

-- **TV series no longer rejected by metadata year filter when downloading later seasons**
Searching metadata for a later season episode (e.g. Death in Paradise S03 from 2014) could cause the series to be rejected because the episode air year didn't match the series premiere year (2011). The year filter now only applies to movies.

-- **Folder names no longer use dots instead of spaces**
In some cases folder names were produced with dots (`Tip.Toe`) instead of spaces (`Tip Toe`). The formatter now defaults to spaces correctly.

-- **Trailing space in folder name no longer crashes downloads**
When a title had no year, the folder name template could produce a trailing space (e.g. `Vera `) which Windows cannot create as a folder, crashing the download. Folder names are now trimmed.

-- **Help Page** New Download Folder Structure section explaining the TV and movie folder layouts and how to customise them via Envied Config.

-- **Service updates (synced from upstream)**
NINE: improved subtitle track filtering and HTTPS source selection. TPTV: terminal search (`envied search TPTV`) now works correctly. STV: updated to v1.0.4. TEN: fixed missing import that could cause errors.


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
