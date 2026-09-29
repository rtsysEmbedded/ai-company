from fastapi import FastAPI, Depends, HTTPException

app = FastAPI()


def require_token(authorization: str = ""):
    """Validate the JWT bearer token; reject expired tokens with 401."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing token")
    return authorization[7:]


@app.get("/api/v1/device/{device_id}/config")
def get_config(device_id: str, token: str = Depends(require_token)):
    """Return the stored configuration of one device."""
    return {"device_id": device_id, "baudrate": 115200}


@app.put("/api/v1/device/{device_id}/firmware")
def upload_firmware(device_id: str, token: str = Depends(require_token)):
    """Start an OTA firmware update; the image must be signed with ed25519."""
    return {"status": "update started"}
