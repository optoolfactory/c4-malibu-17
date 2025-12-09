#!/usr/bin/env python3
import requests
import zipfile

from pathlib import Path

from openpilot.frogpilot.common import frogpilot_utilities, frogpilot_variables

GITHUB_PING_URL = "https://raw.githubusercontent.com"
GITHUB_URL = f"https://raw.githubusercontent.com/{frogpilot_variables.RESOURCES_REPO}"
GITLAB_PING_URL = f"https://gitlab.com/{frogpilot_variables.RESOURCES_REPO}"
GITLAB_URL = f"https://gitlab.com/{frogpilot_variables.RESOURCES_REPO}/-/raw"

GIF_SIGNATURES = (b"GIF87a", b"GIF89a")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def download_file(cancel_param, destination, download_param, params_memory, progress_param, session, url, offset_bytes=0, total_bytes=0):
  temp_file_path = destination.with_suffix(destination.suffix + ".tmp")

  try:
    destination.parent.mkdir(parents=True, exist_ok=True)

    with session.get(url, stream=True, timeout=10) as response:
      if response.status_code == 404 and url.endswith(".gif"):
        print(f"GIF download failed (404). Attempting fallback to PNG for {destination.name}")
        return download_file(cancel_param, destination.with_suffix(".png"), download_param, params_memory, progress_param, session, url.replace(".gif", ".png"), offset_bytes, total_bytes)

      response.raise_for_status()

      total_size = get_content_length(response)

      with temp_file_path.open("wb") as temp_file:
        downloaded_size = 0

        for chunk in response.iter_content(chunk_size=16384):
          if params_memory.get_bool(cancel_param):
            raise InterruptedError

          if not chunk:
            continue

          temp_file.write(chunk)
          downloaded_size += len(chunk)

          if total_bytes:
            overall_progress = (offset_bytes + downloaded_size) / total_bytes * 100
          elif total_size and total_size > 0:
            overall_progress = downloaded_size / total_size * 100
          else:
            overall_progress = 0

          if total_size is None and not total_bytes:
            params_memory.put(progress_param, "Downloading...")
          elif overall_progress < 100:
            params_memory.put(progress_param, f"{overall_progress:.0f}%")
          else:
            params_memory.put(progress_param, "Verifying download...")

      temp_file_path.replace(destination)
      return destination, None, False

  except InterruptedError:
    temp_file_path.unlink(missing_ok=True)
    return None, "Download cancelled...", True
  except Exception as error:
    temp_file_path.unlink(missing_ok=True)
    error_message, _, _ = handle_request_error(error)
    return None, f"Failed: {error_message}", False


def get_content_length(response):
  content_length = response.headers.get("Content-Length")
  if content_length is None:
    return None

  try:
    return int(content_length)
  except ValueError:
    return None


def get_remote_file_size(session, url):
  headers = {"Accept-Encoding": "identity"}

  try:
    response = session.head(url, headers=headers, timeout=10, allow_redirects=True)
    if response.status_code in (405, 501):
      return get_remote_file_size_from_get(session, url, headers)

    response.raise_for_status()

    remote_size = get_content_length(response)
    if remote_size is not None:
      return remote_size

    return get_remote_file_size_from_get(session, url, headers)

  except Exception as error:
    error_message, _, _ = handle_request_error(error)
    print(f"Failed to determine remote file size for {url}: {error_message}")
    return None


def get_remote_file_size_from_get(session, url, headers):
  with session.get(url, headers=headers, stream=True, timeout=10, allow_redirects=True) as response:
    response.raise_for_status()

    remote_size = get_content_length(response)
    if remote_size is not None:
      return remote_size

    return sum(len(chunk) for chunk in response.iter_content(chunk_size=16384) if chunk)


def get_repository_sources():
  repository_sources = []

  if frogpilot_utilities.is_url_pingable(GITHUB_PING_URL):
    repository_sources.append(("github", "GitHub", GITHUB_URL))
  if frogpilot_utilities.is_url_pingable(GITLAB_PING_URL):
    repository_sources.append(("gitlab", "GitLab", GITLAB_URL))

  return repository_sources


def handle_error(destination, download_param, error, error_message, params_memory, progress_param):
  cleanup_download_target(destination)

  if error is not None:
    print(f"Error occurred: {error}")

  if progress_param:
    params_memory.put(progress_param, error_message)
  if download_param:
    params_memory.remove(download_param)


def handle_request_error(error):
  if isinstance(error, requests.exceptions.HTTPError) and error.response is not None:
    status_code = error.response.status_code
    return f"Server error ({status_code})", status_code in {408, 409, 425, 429, 500, 502, 503, 504}, status_code
  if isinstance(error, (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError)):
    return "Connection dropped", True, None
  if isinstance(error, requests.exceptions.ReadTimeout):
    return "Read timed out", True, None
  if isinstance(error, requests.exceptions.Timeout):
    return "Download timed out", True, None
  if isinstance(error, requests.exceptions.RequestException):
    return "Network request error. Check connection", True, None
  return "Unexpected error", False, None


def cleanup_download_target(destination):
  if destination is None:
    return

  destination_path = Path(destination)
  if destination_path.is_file() or destination_path.is_symlink():
    destination_path.unlink(missing_ok=True)
  elif destination_path.is_dir():
    frogpilot_utilities.delete_file(destination_path)


def verify_download(file_path, session, url):
  if not file_path.is_file():
    return False, f"Downloaded file is missing: {file_path.name}"

  verified_url = url.replace(".gif", ".png") if file_path.suffix == ".png" and url.endswith(".gif") else url

  remote_file_size = get_remote_file_size(session, verified_url)
  local_size = file_path.stat().st_size
  if remote_file_size is not None and remote_file_size != local_size:
    return False, f"File size mismatch for {file_path.name}: Remote {remote_file_size} vs Local {local_size}"

  file_type_error = verify_file_type(file_path)
  if file_type_error is not None:
    return False, file_type_error

  return True, None


def verify_file_type(file_path):
  file_suffix = file_path.suffix.lower()

  if file_suffix == ".zip":
    if not zipfile.is_zipfile(file_path):
      return f"Downloaded ZIP is invalid: {file_path.name}"

    with zipfile.ZipFile(file_path) as archive:
      bad_member = archive.testzip()
      if bad_member is not None:
        return f"Downloaded ZIP contains a corrupt file: {bad_member}"
      if not archive.namelist():
        return f"Downloaded ZIP is empty: {file_path.name}"
    return None

  with file_path.open("rb") as file:
    header = file.read(8)

  if file_suffix == ".png" and header != PNG_SIGNATURE:
    return f"Downloaded PNG has an invalid signature: {file_path.name}"
  if file_suffix == ".gif" and not header.startswith(GIF_SIGNATURES):
    return f"Downloaded GIF has an invalid signature: {file_path.name}"

  return None
