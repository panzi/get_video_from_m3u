get\_video\_from\_m3u
=====================

This script downloads MPEG-TS chunks as referred by a given m3u playlist
into a local file. Use this if you want to watch a streamed video on
another device or if you want to buffer it as a whole at maximum quality,
even though your internet connection sucks.

Usage
-----

Start video playback in a browser (e.g. Chrome), open the developer tools,
search the network tab for an .m3u or .m3u8 URL, right-click -> copy as cURL,
start this script, choose an output file and then paste what you copied into
the text input field.

Arguments that are not provided on the command line are asked for in the GUI.

	python get_video_from_m3u.py [options] [--] [output file name] [URL or cURL]

### Options

	--gui             Use KDE GUI (default if kdialog exists)
	--no-gui          No GUI, only output text on command line
	--live-assemble   experimental and currently broken!
	--ffmpeg          Pipe concatenated chunks through ffmpeg to properly
	                  recreate container. (default if ffmpeg exists)
	--no-ffmpeg       Just concatenate the downloaded chunks.
	--keep-cache      Keep cache folder and files after finishing download.

Dependencies
------------

 * [Python](https://www.python.org/)
 * [Requests: HTTP for Humans](http://docs.python-requests.org/en/latest/)
 * [DBus-Python](https://pypi.python.org/pypi/dbus-python/) (optional for KDE GUI)
 * [KDE](https://www.kde.org/) (for kdialog, optional)
 * [lxml](http://lxml.de)
