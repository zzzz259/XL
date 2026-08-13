import os, pathlib, sys, codecs, shutil, subprocess

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
folder4result         = folder_root + "/result"
folder4subcontractors = folder_root + "/_subcontractors"
subfolder             = folder_cur[len(folder_root + "/_tempo/"):]
folder4result         = folder4result + "/" + subfolder

#print(folder_cur, subfolder, folder4result)

subprocess.run([folder4subcontractors + '/fsb_aud_extr.exe', file_fsb])

pathlib.Path(folder4result).mkdir(parents=True, exist_ok=True)

move_result2folder()