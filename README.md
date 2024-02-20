# Download videos and photos from TikTok

## Usage

```console
usage: TT_downloader [-h] [-v] [--list-file [LIST_FILE]]
                     [--archive-file [ARCHIVE_FILE]] --output-name
                     [OUTPUT_NAME] [--log-level {debug,info,warning,error}]
                     [url ...]

Download TikTok videos

positional arguments:
  url                   URL to download.

options:
  -h, --help            show this help message and exit
  -v, --version         show program's version number and exit
  --list-file [LIST_FILE]
                        Text file containing URLs to download.
  --archive-file [ARCHIVE_FILE]
                        Archive file to store downloaded videos, compatible
                        with yt-dlp.
  --output-name [OUTPUT_NAME]
                        Output name for the downloaded videos, available
                        patterns: %description%, %author_id%, %author_name%,
                        %media_height%, %media_width%, %media_id%, %mod_time%
  --log-level {debug,info,warning,error}
                        How much stuff is logged. One of 'debug', 'info',
                        'warning', 'error'.
```

- You can specify one or more URLs and/or specify a text file containing URLs (one on each line). Both options can be
  used at the same time.
- The pattern is required, directories will be automatically created.
- Photos are downloaded with user watermark to get the higher resolution possible.
- This program is intended to be used with the URLs YT-DLP isn't able to download as not all videos are downloaded with
  the higher resolution available.
- The archive file is compatible with yt-dlp's.
- Files modification time is set to the media's upload date.
- Files won't be overwritten, if the output file already exists, a suffix is added to the new file.
- If the ID is present in the archive file the url will be skipped, if it's not present and the file already exists it
  won't be neither skipped nor overwritten, a suffix will be appended.
- The description max length is 190 characters, if it's longer the rest of the description is discarded.
- The username max length is 40 characters, if it's longer the rest of the username is discarded.

Examples:

```shell
TT_downloader https://www.tiktok.com/@xxxxxxx/photo/YYYYYYY https://www.tiktok.com/@xxxxxxx/video/WWWWWW --list-file urls.txt --pattern "%media_id%"
```

```shell
TT_downloader https://www.tiktok.com/@xxxxxxx/video/YYYYYYY --pattern "%media_id%"
```

```shell
TT_downloader --list-file urls.txt --pattern "TT/%author_id%/%media_id%"
```
