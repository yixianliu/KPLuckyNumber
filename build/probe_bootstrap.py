import sys, os
sys.path.insert(0, r"D:\PythonProject\KPLuckyNumber")
sys.frozen = True
sys.executable = r"D:\PythonProject\KPLuckyNumber\dist\KPLuckyNumber\KPLuckyNumber.exe"
import config
print("IS_FROZEN=", config.IS_FROZEN)
print("CREATED=", config.FIRST_RUN_ENV_CREATED)
print("ENV=", config.ENV_FILE_PATH)
print("EXISTS=", os.path.isfile(config.ENV_FILE_PATH))
