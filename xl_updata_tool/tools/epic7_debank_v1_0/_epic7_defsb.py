import codecs
import json
import os
import pathlib
import shutil
import sys

from fsb_fallback import extract_fsb_with_fallback


STATUS_FILE = ".extract_status.json"


def write_status(status, returncode=None, method=None):
  payload = {"status": status}
  if returncode is not None:
    payload["returncode"] = returncode
  if method:
    payload["method"] = method
  with open(os.path.join(folder4result, STATUS_FILE), "w", encoding="utf-8") as status_file:
    json.dump(payload, status_file, ensure_ascii=False)

def move_result2folder():
  for filename in os.listdir(folder_cur):
    fullname = os.path.join(folder_cur, filename)
    if (os.path.isfile(fullname)) and (not filename.endswith(".fsb")):
      shutil.move(fullname, folder4result + "/" + filename)

os.environ["PYTHONIOENCODING"] = "utf-8"
encoding                       = "utf-8"
codecs.lookup(encoding)

folder_cur            = os.getcwd()
file_fsb              = sys.argv[1]
folder_root           = sys.argv[2]
folder4result         = sys.argv[3] if len(sys.argv) > 3 else folder_root + "/result"
folder4subcontractors = folder_root + "/_subcontractors"

#print(folder_cur, subfolder, folder4result)

pathlib.Path(folder4result).mkdir(parents=True, exist_ok=True)

extractor_code, method, legacy_code, fallback_code = extract_fsb_with_fallback(
  file_fsb, folder_cur, folder_root, folder4result
)
if extractor_code != 0:
  print(f"FSB extraction failed: legacy={legacy_code}, fallback={fallback_code}")
  write_status("failed", extractor_code, "fsb_aud_extr+vgmstream")
  sys.exit(1)

if method == "vgmstream":
  write_status("success", 0, method)
  sys.exit(0)

move_result2folder()
write_status("success", 0, method)
