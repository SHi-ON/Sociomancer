import csv
import time
import subprocess

csv_file = "Connections.csv"

with open(csv_file, newline='') as file:
    reader = csv.DictReader(file)
    for i, row in enumerate(reader):
        if i == 4:
           break
        url = row["URL"]
        subprocess.run(["open", "-a", "Safari", url])
        time.sleep(0.5)  # slight delay so Safari can open each in new tab
