Download the metadata of a TIDAL Playlist to parse and use for other tools.

You can download this locally and provide a CLIENT_ID and a CLIENT_SECRET through a .env file.

To enroll in the TIDAL Developer Program and create a Third-Party App go to: [Tidal Developer](https://developer.tidal.com/)

For batch downloading the info of many playlists, enter their URL's in a line seperated list in a .txt file, and then choose it through the file explorer.

The included playlistparser.py can be used to parse the JSON output of the tidalplaylist.py script and compare it with your local library of music.
It will then output the list of missing albums to a CSV file to be parsed by other applications to batch download or find them.

The include makecue.py script can be used to take the JSON output of the tidalplaylist.py script and compare it with your local library of music.
It will then output a series of .cue files so that you may listen to your playlists locally.

If you are looking for a way to convert the .cue files to .m3u I reccomend this repo: [cue_to_m3u converter](https://github.com/EcoG-One/cue_to_m3u_converter)

The listofplaylists.txt file is an example of how to arrange playlists to download them, it's also an easy way for me to track my playlists.
