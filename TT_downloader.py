import argparse
import copy
import json
import logging
import os.path
import re
import sys
from typing import Optional, List, Tuple

import requests

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

PLATFORM = sys.platform

PATTERNS_TEMPLATE = {
    PATTERN_DESC: None,
    PATTERN_MOD_TIME: None,
    PATTERN_AUTHOR_ID: None,
    PATTERN_AUTHOR_NAME: None,
    PATTERN_MEDIA_HEIGHT: None,
    PATTERN_MEDIA_WIDTH: None,
    PATTERN_MEDIA_ID: None
}

global patterns


def main():
    parser = argparse.ArgumentParser(prog="TT_downloader",
                                     description="Download TikTok videos")
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
                             "%description%, %author_id%, %author_name%, %media_height%, %media_width%, %media_id%, "
                             "%mod_time%",
                        required=True,
                        nargs="?")
    parser.add_argument("--log-level",
                        help="How much stuff is logged. One of 'debug', 'info', 'warning', 'error'.",
                        default="warning",
                        choices=["debug", "info", "warning", "error"],
                        type=str.lower)

    args = parser.parse_args()

    url = args.url
    url_list_file = args.list_file
    archive_file = args.archive_file
    output_name = args.output_name

    log_level = logging.getLevelName(args.log_level.upper())
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s: %(message)s")

    if len(url) == 0 and url_list_file is None:
        logging.critical("No URL or list file was provided.")
        sys.exit(1)

    if os.path.isdir(archive_file):
        logging.critical(f"Archive filepath is a directory: {archive_file}")
        sys.exit(1)

    url_list = []
    if url_list_file is not None:
        with open(url_list_file, "r") as f:
            file_content = f.read().splitlines()
            for line in file_content:
                if line.strip() != "":
                    url_list.append(line.strip())

    for item in url:
        url_list.append(item)

    for url in url_list:
        url_sanitized = sanitize_url(url)
        if url_sanitized is None:
            logging.warning(f"Skipping {url}")

        download_success, media_id, already_downloaded = download_media(url=url_sanitized,
                                                                        output_name=output_name,
                                                                        archive_file=archive_file)

        if already_downloaded:
            print(f"Already downloaded: {media_id}")
            continue

        if download_success:
            print(f"Download successful: {media_id}")
            add_to_archive(archive_file, media_id)
        else:
            logging.error(f"Failed to download: {media_id}")

    print("\n" + "All done!")


def download_media(url: str,
                   output_name: str,
                   archive_file: Optional[str]) -> Tuple[bool, Optional[str], bool]:
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

    setup_patterns(data["aweme_list"][0])

    if is_photo:
        if download_photos(data["aweme_list"][0]["image_post_info"]["images"],
                           output_name, media_id, patterns.get(PATTERN_MOD_TIME, 0)):
            return True, media_id, False
    else:
        for vid_url in data["aweme_list"][0]["video"]["play_addr"]["url_list"]:
            if download_video(vid_url, output_name, patterns.get(PATTERN_MOD_TIME, 0)):
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
        for pattern in patterns.keys():
            value = sanitize_pattern(str(patterns.get(pattern, "")))
            output_name = re.sub("%" + re.escape(pattern) + "%", value, output_name, flags=re.IGNORECASE)
    else:
        for pattern in patterns.keys():
            if pattern in ignore_patterns:
                continue
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
                   mod_time: int) -> bool:
    output_file = os.path.abspath(get_output_name(output_name))

    if not output_file.lower().endswith(".mp4"):
        output_file += ".mp4"

    if os.path.exists(output_file):
        logging.warning(f"File \"{output_file}\" already exists. Padding filename.")
        output_file = pad_filename(output_file)

    return download_data(url, output_file, mod_time)


def download_data(url: str,
                  output_file: str,
                  mod_time: int) -> bool:
    sess = requests.session()
    sess.headers.update({"User-Agent": USER_AGENT})

    response = sess.get(url, stream=True, allow_redirects=True)

    if response.status_code != 200:
        logging.error(f"Error downloading {url}")
        return False

    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    try:
        stream_file = open(output_file.encode(encoding="utf-8"), mode="wb", buffering=0)
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

    if mod_time != 0:
        try:
            os.utime(output_file, (mod_time, mod_time))
        except PermissionError as e:
            logging.error(e)

    return True


def download_photos(data: List[dict],
                    output_name: str,
                    media_id: str,
                    mod_time: int) -> bool:

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
            if download_data(url, output_file, mod_time):
                success = True
                break
        results.append(success)
    else:
        for idx, image in enumerate(data):
            success = False
            for url in reversed(image["owner_watermark_image"]["url_list"]):
                if download_data(url, output_file[idx], mod_time):
                    success = True
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


def setup_patterns(data: dict) -> None:
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

    if len(description) > 190:
        description = description[:190]

    if len(author_name) > 40:
        author_name = author_name[:40]

    patterns[PATTERN_DESC] = description.strip()
    patterns[PATTERN_MOD_TIME] = mod_time
    patterns[PATTERN_AUTHOR_ID] = author_id.strip()
    patterns[PATTERN_AUTHOR_NAME] = author_name.strip()
    patterns[PATTERN_MEDIA_HEIGHT] = media_height
    patterns[PATTERN_MEDIA_WIDTH] = media_width
    patterns[PATTERN_MEDIA_ID] = media_id.strip()


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
    with open(archive_file, "a") as f:
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
