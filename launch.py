import json
from threading import Thread
from time import sleep
from urllib.request import urlopen
import webbrowser
import uvicorn

URL = "http://127.0.0.1:18765/"

def open_when_ready():
    for _ in range(40):
        try:
            with urlopen(URL + "api/bootstrap", timeout=1):
                break
        except OSError:
            sleep(.25)
    else:
        return
    webbrowser.open(URL)

if __name__ == "__main__":
    try:
        with urlopen(URL + "openapi.json", timeout=1) as response:
            existing = json.load(response)
        if "/api/evidence" in existing.get("paths", {}):
            webbrowser.open(URL)
            raise SystemExit(0)
    except (OSError, ValueError):
        pass
    print("AGRO MESH - Deja esta ventana abierta mientras usas la aplicacion.", flush=True)
    Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("backend.main:app", host="127.0.0.1", port=18765)

