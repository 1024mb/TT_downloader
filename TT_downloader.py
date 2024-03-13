import argparse
import copy
import json
import logging
import os.path
import re
import shutil
import subprocess
import sys
from datetime import datetime, UTC
from typing import Optional, List, Tuple

import exif
import requests

from __init__ import __version__

API_LIST = (
    "https://api19-core-c-useast1a.musical.ly/aweme/v1/feed/?aweme_id={}",
    "https://api16-normal-c-useast1a.tiktokv.com/aweme/v1/feed/?aweme_id={}",
    "https://api31-normal-useast2a.tiktokv.com/aweme/v1/aweme/detail/?aweme_id={}"
)

API_M = ("&version_code=330304&app_name=musical_ly&channel=App&device_id=null&os_version=16.6&device_platform=iphone"
         "&device_type=iPhone15")

USER_AGENT = ("com.ss.android.ugc.33.3.4/330304 (Linux; U; Android 13; en_US; Pixel 7; Build/TD1A.220804.031; "
              "Cronet/58.0.2991.0)")

REGEX_VIDEO_ID = r"(?:https:\/\/(?:www\.)*tiktok\.com\/@[^?\/]+\/video\/)(?:([0-9]+)?(?:\?.+)?$|$)"
REGEX_PHOTO_ID = r"(?:https:\/\/(?:www\.)*tiktok\.com\/@[^?\/]+\/photo\/)(?:([0-9]+)?(?:\?.+)?$|$)"
REGEX_TIKTOK_URL = r"(https:\/\/(?:www\.)*tiktok\.com\/@[^?\/]+)(?:(\/(?:video|photo)\/[0-9]+)?(\?.+)?$|$)"

PATTERN_DESC = "description"
PATTERN_MOD_TIME = "mod_time"
PATTERN_AUTHOR_ID = "author_id"
PATTERN_AUTHOR_NAME = "author_name"
PATTERN_MEDIA_HEIGHT = "media_height"
PATTERN_MEDIA_WIDTH = "media_width"
PATTERN_MEDIA_ID = "media_id"
PATTERN_COUNTRY = "country_code"
PATTERN_URL = "url"

PLATFORM = sys.platform

PATTERNS_TEMPLATE = {
    PATTERN_DESC: None,
    PATTERN_MOD_TIME: None,
    PATTERN_AUTHOR_ID: None,
    PATTERN_AUTHOR_NAME: None,
    PATTERN_MEDIA_HEIGHT: None,
    PATTERN_MEDIA_WIDTH: None,
    PATTERN_MEDIA_ID: None,
    PATTERN_COUNTRY: None,
    PATTERN_URL: None
}

global patterns


def main():
    parser = argparse.ArgumentParser(prog="TT_downloader",
                                     description="Download TikTok videos")
    parser.add_argument("-v", "--version",
                        action="version",
                        version=f"%(prog)s v{__version__}")
    parser.add_argument("url",
                        help="URL to download.",
                        nargs="*")
    parser.add_argument("--list-file",
                        help="Text file containing URLs to download.",
                        nargs="?",
                        default=None,
                        required=False)
    parser.add_argument("--archive-file",
                        help="Archive file to store downloaded videos, compatible with yt-dlp.",
                        nargs="?",
                        default=None)
    parser.add_argument("--output-name",
                        help="Output name for the downloaded videos, available patterns: "
                             "%%description%%, %%author_id%%, %%author_name%%, %%media_height%%, %%media_width%%, "
                             "%%media_id%%, %%mod_time%%, %%country%%, %%url%%",
                        required=True,
                        nargs="?")
    parser.add_argument("--log-level",
                        help="How much stuff is logged. One of 'debug', 'info', 'warning', 'error'.",
                        default="warning",
                        choices=["debug", "info", "warning", "error"],
                        type=str.lower)
    parser.add_argument("--ffmpeg-path",
                        help="Path to the ffmpeg binary. By default taken from PATH.",
                        default=shutil.which("ffmpeg"),
                        nargs="?")

    args = parser.parse_args()

    url = args.url
    url_list_file = args.list_file
    archive_file = args.archive_file
    output_name = args.output_name
    ffmpeg_path = args.ffmpeg_path

    log_level = logging.getLevelName(args.log_level.upper())
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s: %(message)s")

    if len(url) == 0 and url_list_file is None:
        logging.critical("No URL or list file was provided.")
        sys.exit(1)

    if archive_file is not None and os.path.isdir(archive_file):
        logging.critical(f"Archive filepath is a directory: {archive_file}")
        sys.exit(1)

    if url_list_file is not None:
        if not os.path.exists(url_list_file):
            logging.critical(f"List file does not exist: {url_list_file}")
            sys.exit(1)
        if os.path.isdir(url_list_file):
            logging.critical(f"List filepath is a directory: {url_list_file}")
            sys.exit(1)

    url_list = []
    if url_list_file is not None:
        with open(url_list_file, "r") as f:
            file_content = f.read().splitlines()
            for line in file_content:
                if line.strip() != "":
                    url_list.append(line.strip())

    for item in url:
        url_list.append(item.strip())

    for url in url_list:
        url_sanitized = sanitize_url(url)
        if url_sanitized is None:
            logging.warning(f"Skipping: {url}")
            continue

        download_success, media_id, already_downloaded = download_media(url=url_sanitized,
                                                                        output_name=output_name,
                                                                        archive_file=archive_file,
                                                                        ffmpeg_path=ffmpeg_path)

        if already_downloaded:
            print(f"Already downloaded: {media_id}")
            continue

        if download_success:
            print(f"Download successful: {media_id}")
            if archive_file is not None:
                add_to_archive(archive_file, media_id)
        else:
            logging.error(f"Failed to download: {media_id}")

    print("\n" + "All done!")


def download_media(url: str,
                   output_name: str,
                   archive_file: Optional[str],
                   ffmpeg_path: Optional[str]) -> Tuple[bool, Optional[str], bool]:
    global patterns
    patterns = copy.deepcopy(PATTERNS_TEMPLATE)  # reset patterns values

    try:
        media_id = re.search(REGEX_VIDEO_ID, url).group(1)
        is_photo = False
    except (TypeError, AttributeError, IndexError):
        try:
            media_id = re.search(REGEX_PHOTO_ID, url).group(1)
            is_photo = True
        except (TypeError, AttributeError, IndexError):
            return False, None, False

    if archive_file is not None and os.path.exists(archive_file):
        downloaded_ids = get_already_downloaded_ids(archive_file)
    else:
        downloaded_ids = []

    if "tiktok " + media_id in downloaded_ids:
        return False, media_id, True

    i = 0
    i_max = len(API_LIST) - 1
    data = None

    while data is None and i <= i_max:
        data = get_api_data(media_id, i)
        i += 1
        try:
            if data["aweme_list"][0]["aweme_id"] != media_id:
                data = None
        except (TypeError, AttributeError, IndexError):
            data = None

    if data is None:
        return False, media_id, False

    if data["aweme_list"][0]["aweme_id"] != media_id:
        return False, media_id, False

    setup_patterns(data["aweme_list"][0], url)

    if is_photo:
        if download_photos(data["aweme_list"][0]["image_post_info"]["images"],
                           output_name, media_id, patterns.get(PATTERN_MOD_TIME, 0), ffmpeg_path):
            return True, media_id, False
    else:
        for vid_url in data["aweme_list"][0]["video"]["play_addr"]["url_list"]:
            if download_video(vid_url, output_name, patterns.get(PATTERN_MOD_TIME, 0), ffmpeg_path):
                return True, media_id, False

    return False, media_id, False


def get_already_downloaded_ids(archive_file: str) -> List[str]:
    id_list = []
    with open(archive_file, "r", encoding="utf-8") as f:
        for line in f.read().splitlines():
            id_list.append(line)

    return copy.deepcopy(id_list)


def get_output_name(output_name: str,
                    ignore_patterns: list = None) -> str:
    global patterns

    if ignore_patterns is None:
        ignore_patterns = []

    for pattern in patterns.keys():
        if pattern in ignore_patterns:
            continue
        elif pattern == PATTERN_DESC:
            value = sanitize_pattern(str(patterns.get(pattern, "")))[:190]
        elif pattern == PATTERN_AUTHOR_NAME:
            value = sanitize_pattern(str(patterns.get(pattern, "")))[:40]
        else:
            value = sanitize_pattern(str(patterns.get(pattern, "")))
        output_name = re.sub("%" + re.escape(pattern) + "%", value, output_name, flags=re.IGNORECASE)

    if output_name.strip() == "":
        output_name = "_"

    return output_name.strip()


def sanitize_pattern(string: str) -> str:
    illegal_characters = {}

    if PLATFORM == "win32" or PLATFORM == "msys" or PLATFORM == "cygwin":
        illegal_characters = {
            "<": "\uFE64",
            ">": "\uFE65",
            ":": "\uFE55",
            "\"": "\uFF02",
            "/": "\uFF0F",
            "\\": "\uFF3C",
            "|": "\uFF5C",
            "?": "\uFF1F",
            "*": "\uFF0A"
        }

    if PLATFORM == "darwin" or PLATFORM == "linux":
        illegal_characters = {"/": "\uFF0F"}

    for character in illegal_characters.keys():
        if character in string:
            string = string.replace(character, illegal_characters[character].encode("utf-8").decode("utf-8"))

    return string


def download_video(url: str,
                   output_name: str,
                   mod_time: int,
                   ffmpeg_path: Optional[str]) -> bool:
    output_file = os.path.abspath(get_output_name(output_name))

    if not output_file.lower().endswith(".mp4"):
        output_file += ".mp4"

    if os.path.exists(output_file):
        logging.warning(f"File \"{output_file}\" already exists. Padding filename.")
        output_file = pad_filename(output_file)

    success = download_data(url, output_file)

    if success:
        if ffmpeg_path is not None:
            add_tags_video(output_file, ffmpeg_path)
        restore_modtime(output_file, mod_time)

    return success


def download_data(url: str,
                  output_file: str) -> bool:
    sess = requests.session()
    sess.headers.update({"User-Agent": USER_AGENT})

    response = sess.get(url, stream=True, allow_redirects=True)

    if response.status_code != 200:
        logging.error(f"Error downloading {url}")
        return False

    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    if PLATFORM == "win32" or PLATFORM == "msys" or PLATFORM == "cygwin":
        output_file = "\\\\?\\".encode(encoding="utf-8") + output_file.encode(encoding="utf-8")
    else:
        output_file = output_file.encode(encoding="utf-8")

    try:
        stream_file = open(output_file, mode="wb", buffering=0)
    except UnicodeEncodeError as e:
        logging.critical(f"Could not create file: {output_file}")
        logging.critical(e)
        try:
            os.remove(output_file)
        except FileNotFoundError:
            pass
        sys.exit(1)
    except PermissionError as e:
        logging.critical(f"Could not create file: {output_file}")
        logging.critical(e)
        try:
            os.remove(output_file)
        except FileNotFoundError:
            pass
        sys.exit(1)

    try:
        for chunk in response:
            stream_file.write(chunk)
            stream_file.flush()
    except Exception as e:
        logging.error(e)
        stream_file.close()
        response.close()
        sess.close()
        try:
            os.remove(output_file)
        except FileNotFoundError:
            pass
        return False

    stream_file.close()
    response.close()
    sess.close()

    return True


def download_photos(data: List[dict],
                    output_name: str,
                    media_id: str,
                    mod_time: int,
                    ffmpeg_path: Optional[str]) -> bool:

    image_number = len(data)

    if image_number == 0:
        logging.error(f"No image URLs found for {media_id}")
        return False

    name, ext = os.path.splitext(output_name)
    if ext == "":
        output_name += ".jpg"
    elif ext.lower() != ".jpg":
        output_name = name + ".jpg"

    if image_number == 1:
        setup_pattern_image(data[0])
        output_file = os.path.abspath(get_output_name(output_name))

        if os.path.exists(output_file):
            logging.warning(f"File \"{output_file}\" already exists. Padding filename.")
            output_file = pad_filename(output_file)
    else:
        output_file = os.path.abspath(get_output_name(output_name, ["media_height", "media_width"]))
        orig_output_file = output_file
        output_file = []
        pad_number = max(len(str(image_number)), 2)

        for i in range(1, image_number + 1):
            setup_pattern_image(data[i - 1])
            name, ext = os.path.splitext(orig_output_file)
            name = get_output_name(name)
            output_file.append(name + "_" + str(i).zfill(pad_number) + ext)

        aux_list = copy.deepcopy(output_file)

        for idx, filename in enumerate(aux_list):
            if os.path.exists(filename):
                logging.warning(f"File \"{filename}\" already exists. Padding filename.")
                output_file[idx] = pad_filename(filename)

    results = []

    if image_number == 1:
        success = False
        for url in reversed(data[0]["owner_watermark_image"]["url_list"]):
            success = download_data(url, output_file)
            if success:
                add_tags_photo(output_file)
                restore_modtime(output_file, mod_time)
                break
        results.append(success)
    else:
        for idx, image in enumerate(data):
            success = False
            for url in reversed(image["owner_watermark_image"]["url_list"]):
                success = download_data(url, output_file[idx])
                if success:
                    add_tags_photo(output_file)
                    restore_modtime(output_file, mod_time)
                    break
            results.append(success)

    if all(results):
        return True
    else:
        return False


def pad_filename(filename: str) -> str:
    pad_number = 1
    orig_filename = filename

    while os.path.exists(filename):
        name, ext = os.path.splitext(orig_filename)
        filename = name + "_" + str(pad_number).zfill(2) + ext
        pad_number += 1

    return filename


def setup_pattern_image(data: dict) -> None:
    global patterns

    try:
        media_height = data["owner_watermark_image"]["height"]  # type: Optional[int]
    except (TypeError, AttributeError, IndexError):
        media_height = None
    try:
        media_width = data["owner_watermark_image"]["width"]  # type: Optional[int]
    except (TypeError, AttributeError, IndexError):
        media_width = None

    patterns[PATTERN_MEDIA_HEIGHT] = media_height
    patterns[PATTERN_MEDIA_WIDTH] = media_width


def setup_patterns(data: dict,
                   url: str) -> None:
    global patterns

    try:
        description = data["desc"]  # type: Optional[str]
    except (TypeError, AttributeError, IndexError):
        description = None
    try:
        media_id = data["aweme_id"]  # type: Optional[str]
    except (TypeError, AttributeError, IndexError):
        media_id = None
    try:
        mod_time = data["create_time"]  # type: Optional[int]
    except (TypeError, AttributeError, IndexError):
        mod_time = None
    try:
        author_id = data["author"]["uid"]  # type: Optional[str]
    except (TypeError, AttributeError, IndexError):
        author_id = None
    try:
        author_name = data["author"]["unique_id"]  # type: Optional[str]
    except (TypeError, AttributeError, IndexError):
        author_name = None
    try:
        media_height = data["video"]["play_addr"]["height"]  # type: Optional[int]
    except (TypeError, AttributeError, IndexError):
        media_height = None
    try:
        media_width = data["video"]["play_addr"]["width"]  # type: Optional[int]
    except (TypeError, AttributeError, IndexError):
        media_width = None
    try:
        country = data["region"]  # type: Optional[str]
    except (TypeError, AttributeError, IndexError):
        country = None

    patterns[PATTERN_DESC] = description.strip()
    patterns[PATTERN_MOD_TIME] = mod_time
    patterns[PATTERN_AUTHOR_ID] = author_id.strip()
    patterns[PATTERN_AUTHOR_NAME] = author_name.strip()
    patterns[PATTERN_MEDIA_HEIGHT] = media_height
    patterns[PATTERN_MEDIA_WIDTH] = media_width
    patterns[PATTERN_MEDIA_ID] = media_id.strip()
    patterns[PATTERN_COUNTRY] = country.strip()
    patterns[PATTERN_URL] = url


def add_tags_video(output_file: str,
                   ffmpeg_path: str) -> None:
    global patterns

    name, ext = os.path.splitext(output_file)

    temp_output_file = name + "-temp" + ext

    metadata_args = []

    metadata = {
        "comment": patterns.get(PATTERN_URL, ""),
        "purl": patterns.get(PATTERN_URL, ""),
        "description": patterns.get(PATTERN_DESC, ""),
        "synopsis": patterns.get(PATTERN_DESC, ""),
        "artist": patterns.get(PATTERN_AUTHOR_NAME, ""),
        "country": patterns.get(PATTERN_COUNTRY, "")
    }

    if patterns.get(PATTERN_MOD_TIME, 0) != 0:
        metadata["date"] = datetime.fromtimestamp(patterns.get(PATTERN_MOD_TIME), UTC).strftime("%Y%m%d")

    for key, value in metadata.items():
        metadata_args.extend(["-metadata", f"{key}={value}"])

    cmd_args = [
        ffmpeg_path,
        "-i", output_file,
        "-movflags", "use_metadata_tags",
        "-map_metadata", "0",
        *metadata_args,
        "-c", "copy", "-y",
        temp_output_file
    ]

    proc = subprocess.run(cmd_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if proc.returncode != 0:
        logging.error(f"Error adding tags to {output_file}")
        try:
            os.remove(temp_output_file)
        except FileNotFoundError:
            pass
    else:
        os.remove(output_file)
        os.rename(temp_output_file, output_file)


def add_tags_photo(output_file: str) -> None:
    global patterns
    name, ext = os.path.splitext(output_file)
    tmp_file = name + "-temp" + ext

    mod_time = datetime.fromtimestamp(patterns.get(PATTERN_MOD_TIME, 0), UTC).strftime("%Y:%m:%d %H:%M:%S")

    os.rename(output_file, tmp_file)

    with open(tmp_file, mode="rb") as file:
        image_file = exif.Image(file)

    image_file.image_description = patterns.get(PATTERN_DESC, "").encode("ascii", "backslashreplace").decode("ascii")
    image_file.artist = patterns.get(PATTERN_AUTHOR_NAME, "").encode("ascii", "backslashreplace").decode("ascii")
    image_file.user_comment = patterns.get(PATTERN_URL, "").encode("ascii", "backslashreplace").decode("ascii")

    if patterns.get(PATTERN_MOD_TIME, 0) != 0:
        image_file.datetime_original = mod_time.encode("ascii", "backslashreplace").decode("ascii")
        image_file.datetime_digitized = mod_time.encode("ascii", "backslashreplace").decode("ascii")

    with open(output_file, mode="wb") as file:
        file.write(image_file.get_file())

    os.remove(tmp_file)


def restore_modtime(file: str,
                    mod_time: int) -> None:
    if mod_time != 0:
        try:
            os.utime(file, (mod_time, mod_time))
        except PermissionError as e:
            logging.error(e)


def get_api_data(media_id: str,
                 index: int) -> Optional[dict]:
    sess = requests.session()
    sess.headers.update({"User-Agent": USER_AGENT})

    response = sess.get(API_LIST[index].format(media_id) + API_M)

    if response.status_code != 200:
        logging.error(f"Error getting API data for {media_id}.\nStatus Code: {response.status_code}")
        return None

    api_content = response.content.decode(encoding="utf-8", errors="backslashreplace")

    try:
        vid_data = json.loads(api_content)
    except json.decoder.JSONDecodeError as e:
        logging.error(f"Error decoding response content for {media_id}.\nError: {e}")
        return None

    return copy.deepcopy(vid_data)


def add_to_archive(archive_file: str,
                   media_id: str) -> None:
    with open(archive_file, "a", encoding="utf-8", errors="slashreplace") as f:
        try:
            f.write("tiktok " + media_id + "\n")
        except OSError as e:
            logging.error(f"Couldn't add {media_id} to archive file.")
            logging.error(f"Error: {e}")


def sanitize_url(url: str) -> Optional[str]:
    url = re.search(REGEX_TIKTOK_URL, url, re.IGNORECASE)
    if url is None:
        return None

    try:
        url = url.group(1) + url.group(2)
    except IndexError:
        url = url.group(1)

    return url


if __name__ == "__main__":
    main()
